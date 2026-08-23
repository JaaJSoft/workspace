"""WOPI host endpoints: CheckFileInfo, GetFile, PutFile and lock operations."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workspace.files.models import File, FileEvent
from workspace.files.services import FileService
from workspace.files.services.wopi.tokens import mint_access_token

User = get_user_model()


class WopiViewTestBase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="wopi_owner", email="owner@test.com", password="pw"
        )
        self.other = User.objects.create_user(
            username="wopi_other", email="other@test.com", password="pw"
        )
        self.file = FileService.create_file(
            owner=self.owner,
            name="report.docx",
            content=SimpleUploadedFile(
                "report.docx",
                b"PK\x03\x04 fake docx bytes",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            acting_user=self.owner,
        )

    def _token(self, user=None, can_write=True):
        return mint_access_token(user or self.owner, self.file.uuid, can_write)

    def _file_url(self, token):
        url = reverse("wopi-file", kwargs={"uuid": self.file.uuid})
        return f"{url}?access_token={token}"

    def _contents_url(self, token):
        url = reverse("wopi-file-contents", kwargs={"uuid": self.file.uuid})
        return f"{url}?access_token={token}"


class CheckFileInfoTests(WopiViewTestBase):
    def test_returns_file_metadata(self):
        resp = self.client.get(self._file_url(self._token()))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["BaseFileName"], "report.docx")
        self.assertEqual(data["Size"], self.file.size)
        self.assertTrue(data["UserCanWrite"])
        self.assertTrue(data["SupportsLocks"])
        self.assertTrue(data["UserCanNotWriteRelative"])

    def test_read_only_token_reports_read_only(self):
        resp = self.client.get(self._file_url(self._token(can_write=False)))
        data = resp.json()
        self.assertFalse(data["UserCanWrite"])
        self.assertTrue(data["ReadOnly"])

    def test_missing_or_invalid_token_is_401(self):
        url = reverse("wopi-file", kwargs={"uuid": self.file.uuid})
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(
            self.client.get(f"{url}?access_token=garbage").status_code, 401
        )

    def test_user_without_access_is_404(self):
        # A valid token for a user the ACL no longer knows: the live
        # permission check wins over what the token was minted with.
        token = mint_access_token(self.other, self.file.uuid, can_write=True)
        resp = self.client.get(self._file_url(token))
        self.assertEqual(resp.status_code, 404)

    def test_deleted_file_is_404(self):
        File.objects.filter(pk=self.file.pk).update(deleted_at=timezone.now())
        resp = self.client.get(self._file_url(self._token()))
        self.assertEqual(resp.status_code, 404)


class GetFileTests(WopiViewTestBase):
    def test_streams_the_blob(self):
        resp = self.client.get(self._contents_url(self._token()))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            b"".join(resp.streaming_content), b"PK\x03\x04 fake docx bytes"
        )
        self.assertIn("X-WOPI-ItemVersion", resp.headers)


class PutFileTests(WopiViewTestBase):
    def _put(self, body=b"new bytes", token=None, lock=""):
        headers = {"X-WOPI-Override": "PUT"}
        if lock:
            headers["X-WOPI-Lock"] = lock
        return self.client.post(
            self._contents_url(token or self._token()),
            data=body,
            content_type="application/octet-stream",
            headers=headers,
        )

    def test_put_replaces_content_through_the_service(self):
        old_hash = self.file.content_hash
        resp = self._put(b"updated document body")
        self.assertEqual(resp.status_code, 200)
        self.file.refresh_from_db()
        self.assertNotEqual(self.file.content_hash, old_hash)
        self.assertEqual(self.file.size, len(b"updated document body"))
        self.assertFalse(self.file.has_thumbnail)
        with self.file.content.open("rb") as f:
            self.assertEqual(f.read(), b"updated document body")
        # update_content records the event that drives post-upload processing.
        self.assertTrue(
            FileEvent.objects.filter(
                file=self.file, action=FileEvent.Action.CONTENT_REPLACED
            ).exists()
        )

    def test_read_only_token_cannot_write(self):
        resp = self._put(token=self._token(can_write=False))
        self.assertEqual(resp.status_code, 401)
        self.file.refresh_from_db()
        with self.file.content.open("rb") as f:
            self.assertEqual(f.read(), b"PK\x03\x04 fake docx bytes")

    def test_unlocked_put_is_accepted(self):
        """Collabora never locks before saving; a locked-file 409 here would
        reject every save it makes."""
        self.assertEqual(self._put().status_code, 200)

    def test_put_with_mismatching_wopi_lock_is_409(self):
        self.client.post(
            self._file_url(self._token()),
            headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "session-1"},
        )
        resp = self._put(lock="session-2")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.headers["X-WOPI-Lock"], "session-1")

    def test_put_blocked_by_another_users_app_lock(self):
        File.objects.filter(pk=self.file.pk).update(
            locked_by=self.other,
            locked_at=timezone.now(),
            lock_expires_at=timezone.now() + timedelta(minutes=5),
        )
        resp = self._put()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.headers["X-WOPI-Lock"], "")


class LockOperationTests(WopiViewTestBase):
    def _op(self, override, lock="", old_lock="", token=None):
        headers = {"X-WOPI-Override": override}
        if lock:
            headers["X-WOPI-Lock"] = lock
        if old_lock:
            headers["X-WOPI-OldLock"] = old_lock
        return self.client.post(self._file_url(token or self._token()), headers=headers)

    def test_lock_acquire_refresh_unlock_cycle(self):
        self.assertEqual(self._op("LOCK", lock="abc").status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.wopi_lock, "abc")
        self.assertEqual(self.file.locked_by_id, self.owner.pk)
        self.assertTrue(self.file.is_locked())

        self.assertEqual(self._op("REFRESH_LOCK", lock="abc").status_code, 200)
        self.assertEqual(self._op("UNLOCK", lock="abc").status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.wopi_lock, "")
        self.assertIsNone(self.file.locked_by_id)

    def test_get_lock_reports_current_value(self):
        self._op("LOCK", lock="abc")
        resp = self._op("GET_LOCK")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["X-WOPI-Lock"], "abc")

    def test_conflicting_lock_is_409_with_current_value(self):
        self._op("LOCK", lock="abc")
        resp = self._op("LOCK", lock="other")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.headers["X-WOPI-Lock"], "abc")

    def test_unlock_and_relock_via_old_lock(self):
        self._op("LOCK", lock="abc")
        resp = self._op("LOCK", lock="def", old_lock="abc")
        self.assertEqual(resp.status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.wopi_lock, "def")

    def test_unlock_mismatch_is_409(self):
        self._op("LOCK", lock="abc")
        self.assertEqual(self._op("UNLOCK", lock="nope").status_code, 409)

    def test_expired_wopi_lock_can_be_reacquired(self):
        self._op("LOCK", lock="abc")
        File.objects.filter(pk=self.file.pk).update(
            lock_expires_at=timezone.now() - timedelta(minutes=1)
        )
        self.assertEqual(self._op("LOCK", lock="fresh").status_code, 200)

    def test_another_users_app_lock_blocks_wopi_lock(self):
        File.objects.filter(pk=self.file.pk).update(
            locked_by=self.other,
            locked_at=timezone.now(),
            lock_expires_at=timezone.now() + timedelta(minutes=5),
        )
        resp = self._op("LOCK", lock="abc")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.headers["X-WOPI-Lock"], "")

    def test_own_app_lock_is_upgraded_to_the_wopi_lock(self):
        # The in-app viewer takes an app lock before the editor frame loads;
        # the same user's WOPI session must not be fenced out by it.
        File.objects.filter(pk=self.file.pk).update(
            locked_by=self.owner,
            locked_at=timezone.now(),
            lock_expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.assertEqual(self._op("LOCK", lock="abc").status_code, 200)

    def test_read_only_token_cannot_lock(self):
        resp = self._op("LOCK", lock="abc", token=self._token(can_write=False))
        self.assertEqual(resp.status_code, 401)

    def test_missing_lock_header_is_400(self):
        self.assertEqual(self._op("LOCK").status_code, 400)

    def test_unknown_override_is_501(self):
        self.assertEqual(self._op("FROB", lock="abc").status_code, 501)
