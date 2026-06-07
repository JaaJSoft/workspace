from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileShare

User = get_user_model()


class FileLockAPITests(APITestCase):
    """Tests for file locking API endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="alice@test.com",
            password="pass123",
        )
        # ``other`` is a collaborator: alice's file is shared with him r/w,
        # so he can hit the lock endpoint - just like in the real flow
        # where two co-authors race to acquire on the same shared file.
        self.other = User.objects.create_user(
            username="bob",
            email="bob@test.com",
            password="pass123",
        )
        # ``outsider`` has no access to alice's file - used to assert that
        # a UUID alone doesn't grant lock visibility / mutation rights.
        self.outsider = User.objects.create_user(
            username="eve",
            email="eve@test.com",
            password="pass123",
        )
        self.file = File.objects.create(
            owner=self.user,
            name="doc.md",
            node_type=File.NodeType.FILE,
        )
        FileShare.objects.create(
            file=self.file,
            shared_by=self.user,
            shared_with=self.other,
            permission=FileShare.Permission.READ_WRITE,
        )

    def _url(self, uuid=None):
        return f"/api/v1/files/{uuid or self.file.uuid}/lock"

    # ── POST (acquire / renew) ───────────────────────────

    def test_acquire_lock(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.file.refresh_from_db()
        self.assertEqual(self.file.locked_by, self.user)
        self.assertIsNotNone(self.file.lock_expires_at)

    def test_renew_own_lock(self):
        self.client.force_authenticate(self.user)
        self.client.post(self._url())
        self.file.refresh_from_db()
        old_expires = self.file.lock_expires_at

        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.file.refresh_from_db()
        self.assertGreaterEqual(self.file.lock_expires_at, old_expires)

    def test_acquire_conflict(self):
        """Another user cannot acquire a lock held by someone else."""
        self.client.force_authenticate(self.user)
        self.client.post(self._url())

        self.client.force_authenticate(self.other)
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("locked_by", resp.data)

    def test_acquire_expired_lock(self):
        """Can acquire a lock that has expired."""
        File.objects.filter(pk=self.file.pk).update(
            locked_by=self.user,
            locked_at=timezone.now() - timedelta(minutes=10),
            lock_expires_at=timezone.now() - timedelta(minutes=5),
        )
        self.client.force_authenticate(self.other)
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.file.refresh_from_db()
        self.assertEqual(self.file.locked_by, self.other)

    # ── DELETE (release) ─────────────────────────────────

    def test_release_own_lock(self):
        self.client.force_authenticate(self.user)
        self.client.post(self._url())
        resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.file.refresh_from_db()
        self.assertIsNone(self.file.locked_by_id)

    def test_other_user_cannot_force_release_active_lock(self):
        """A collaborator can't clear an active lock held by someone else.

        Otherwise the 409 the POST acquire returns against an active lock
        would be trivially bypassed by issuing DELETE then POST.
        """
        self.client.force_authenticate(self.user)
        self.client.post(self._url())

        self.client.force_authenticate(self.other)
        resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.file.refresh_from_db()
        self.assertEqual(self.file.locked_by, self.user)

    def test_other_user_can_clear_expired_lock(self):
        """An expired lock is cleanup-only, anyone with access can clear it."""
        File.objects.filter(pk=self.file.pk).update(
            locked_by=self.user,
            locked_at=timezone.now() - timedelta(minutes=10),
            lock_expires_at=timezone.now() - timedelta(minutes=5),
        )
        self.client.force_authenticate(self.other)
        resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.file.refresh_from_db()
        self.assertIsNone(self.file.locked_by_id)

    # ── GET (info) ───────────────────────────────────────

    def test_get_lock_info_unlocked(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNone(resp.data["locked_by"])

    def test_get_lock_info_locked(self):
        self.client.force_authenticate(self.user)
        self.client.post(self._url())
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["locked_by"]["username"], "alice")
        self.assertFalse(resp.data["is_expired"])

    # ── Save protection ──────────────────────────────────

    def test_save_blocked_when_locked_by_other(self):
        """PATCH returns 423 when file is locked by another user."""
        self.client.force_authenticate(self.user)
        self.client.post(self._url())

        self.client.force_authenticate(self.other)
        resp = self.client.patch(
            f"/api/v1/files/{self.file.uuid}",
            {"name": "renamed.md"},
        )
        self.assertEqual(resp.status_code, status.HTTP_423_LOCKED)

    def test_save_allowed_for_lock_owner(self):
        """PATCH succeeds for the user who holds the lock."""
        self.client.force_authenticate(self.user)
        self.client.post(self._url())

        resp = self.client.patch(
            f"/api/v1/files/{self.file.uuid}",
            {"name": "renamed.md"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_save_allowed_when_unlocked(self):
        """PATCH succeeds when no lock exists."""
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            f"/api/v1/files/{self.file.uuid}",
            {"name": "renamed.md"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    # ── Edge cases ───────────────────────────────────────

    def test_lock_nonexistent_file(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self._url("00000000-0000-0000-0000-000000000000"))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_lock_requires_authentication(self):
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── Access control ───────────────────────────────────

    def test_get_lock_404_for_user_without_access(self):
        """Knowing a file UUID doesn't grant lock visibility."""
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_acquire_lock_404_for_user_without_access(self):
        """Outsiders can't acquire on a file they have no rights to."""
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.file.refresh_from_db()
        self.assertIsNone(self.file.locked_by_id)

    # ── Expiry boundary ──────────────────────────────────

    def test_is_expired_true_at_exact_boundary(self):
        """At ``lock_expires_at == now``, GET reports ``is_expired=True`` -
        matching the acquire predicate ``lock_expires_at <= now``. Without
        this alignment a client could see ``is_expired=False`` and still
        fail to acquire at the same instant.
        """
        boundary = timezone.now() + timedelta(minutes=1)
        File.objects.filter(pk=self.file.pk).update(
            locked_by=self.user,
            locked_at=boundary - timedelta(minutes=5),
            lock_expires_at=boundary,
        )
        self.client.force_authenticate(self.user)
        with patch("workspace.files.views.timezone.now", return_value=boundary):
            resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["is_expired"])

    def test_release_lock_404_for_user_without_access(self):
        """Outsiders can't release someone else's lock either."""
        self.client.force_authenticate(self.user)
        self.client.post(self._url())

        self.client.force_authenticate(self.outsider)
        resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.file.refresh_from_db()
        self.assertEqual(self.file.locked_by, self.user)

    # ── Concurrent acquire (atomicity) ───────────────────

    def test_acquire_under_race_does_not_overwrite_holder(self):
        """If the lock gets taken between the view's read and its update,
        the conditional UPDATE matches 0 rows and the view returns 409 -
        the original holder's lock survives unchanged.

        Simulates the race window by hooking ``QuerySet.first`` to inject
        alice's acquire just after bob's view reads the file as free.
        """
        from django.db.models.query import QuerySet

        real_first = QuerySet.first
        file_pk = self.file.pk
        injector_user = self.user

        def first_then_inject(qs_self):
            result = real_first(qs_self)
            if (
                isinstance(result, File)
                and result.pk == file_pk
                and result.locked_by_id is None
            ):
                now = timezone.now()
                File.objects.filter(pk=file_pk).update(
                    locked_by=injector_user,
                    locked_at=now,
                    lock_expires_at=now + timedelta(minutes=5),
                )
            return result

        self.client.force_authenticate(self.other)
        with patch.object(QuerySet, "first", first_then_inject):
            resp = self.client.post(self._url())

        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.file.refresh_from_db()
        self.assertEqual(self.file.locked_by, self.user)
