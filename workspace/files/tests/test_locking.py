from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory, APITestCase

from workspace.core.sse_registry import drain_user_events
from workspace.files.models import File, FileShare
from workspace.files.serializers import FileLocked, FileSerializer
from workspace.files.services import FileService
from workspace.files.sse_provider import push_file_event

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
        with patch("workspace.files.viewsets.sync.timezone.now", return_value=boundary):
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


class LockedContentWriteTests(APITestCase):
    """Every content write path answers 423 to a writer who isn't the holder.

    The 423 used to live on ``partial_update`` alone, so a PUT carrying a
    ``content`` part replaced the blob of a file someone else had open in the
    editor - no warning, nothing to restore from. The owner is the one who can
    PUT, and a collaborator editing their file is exactly who the lock is
    there to protect.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@test.com",
            password="pass123",
        )
        self.holder = User.objects.create_user(
            username="holder",
            email="holder@test.com",
            password="pass123",
        )
        self.file = FileService.create_file(
            self.owner,
            "doc.md",
            content=ContentFile(b"original", name="doc.md"),
            mime_type="text/markdown",
        )
        FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with=self.holder,
            permission=FileShare.Permission.READ_WRITE,
        )
        self._lock(self.holder)
        self.client.force_authenticate(self.owner)

    def _lock(self, user, *, expires_in=timedelta(minutes=5)):
        now = timezone.now()
        File.objects.filter(pk=self.file.pk).update(
            locked_by=user,
            locked_at=now,
            lock_expires_at=now + expires_in,
        )

    def _url(self):
        return f"/api/v1/files/{self.file.uuid}"

    def _content(self):
        self.file.refresh_from_db()
        with self.file.content.open("rb") as handle:
            return handle.read()

    def _put(self, **extra):
        return self.client.put(
            self._url(),
            {
                "name": "doc.md",
                "node_type": File.NodeType.FILE,
                "content": SimpleUploadedFile("doc.md", b"clobbered"),
                **extra,
            },
            format="multipart",
        )

    def test_put_with_content_blocked_when_locked_by_other(self):
        resp = self._put()
        self.assertEqual(resp.status_code, status.HTTP_423_LOCKED)
        self.assertEqual(resp.data["locked_by"]["username"], "holder")
        self.assertEqual(self._content(), b"original")

    def test_put_rename_blocked_when_locked_by_other(self):
        resp = self.client.put(
            self._url(),
            {"name": "renamed.md", "node_type": File.NodeType.FILE},
        )
        self.assertEqual(resp.status_code, status.HTTP_423_LOCKED)
        self.file.refresh_from_db()
        self.assertEqual(self.file.name, "doc.md")

    def test_put_allowed_for_lock_holder(self):
        self._lock(self.owner)
        self.assertEqual(self._put().status_code, status.HTTP_200_OK)
        self.assertEqual(self._content(), b"clobbered")

    def test_put_allowed_once_the_lock_expired(self):
        self._lock(self.holder, expires_in=-timedelta(seconds=1))
        self.assertEqual(self._put().status_code, status.HTTP_200_OK)
        self.assertEqual(self._content(), b"clobbered")

    def test_serializer_refuses_a_locked_file_on_its_own(self):
        """The rule holds for callers that never go through the viewset."""
        factory = APIRequestFactory()
        request = Request(factory.patch(self._url()))
        request.user = self.owner
        instance = FileService.annotate_for_serializer(
            File.objects.filter(pk=self.file.pk), self.owner
        ).first()
        serializer = FileSerializer(
            instance,
            data={"content": SimpleUploadedFile("doc.md", b"clobbered")},
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        with self.assertRaises(FileLocked):
            serializer.save()
        self.assertEqual(self._content(), b"original")


class StaleWriteTests(APITestCase):
    """A save carrying the hash it started from is refused once the blob moved.

    The lock covers writers who still hold it; a tab that slept past the
    5-minute TTL wakes up with a buffer loaded before somebody else's edits
    and nothing in its save says so.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="stale",
            email="stale@test.com",
            password="pass123",
        )
        self.file = FileService.create_file(
            self.user,
            "note.md",
            content=ContentFile(b"first", name="note.md"),
            mime_type="text/markdown",
        )
        self.loaded_hash = self.file.content_hash
        self.client.force_authenticate(self.user)

    def _url(self):
        return f"/api/v1/files/{self.file.uuid}"

    def _content(self):
        self.file.refresh_from_db()
        with self.file.content.open("rb") as handle:
            return handle.read()

    def _save(self, body, **extra):
        return self.client.patch(
            self._url(),
            {"content": SimpleUploadedFile("note.md", body), **extra},
            format="multipart",
        )

    def _move_the_blob(self):
        FileService.update_content(
            self.file,
            ContentFile(b"somebody else's paragraphs", name="note.md"),
            name="note.md",
        )

    def test_matching_precondition_saves(self):
        resp = self._save(b"second", **{"base_hash": self.loaded_hash})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self._content(), b"second")

    def test_stale_base_hash_part_is_refused(self):
        self._move_the_blob()
        resp = self._save(b"clobbered", **{"base_hash": self.loaded_hash})
        self.assertEqual(resp.status_code, status.HTTP_412_PRECONDITION_FAILED)
        self.assertEqual(self._content(), b"somebody else's paragraphs")

    def test_stale_if_match_header_is_refused(self):
        self._move_the_blob()
        resp = self.client.patch(
            self._url(),
            {"content": SimpleUploadedFile("note.md", b"clobbered")},
            format="multipart",
            headers={"If-Match": f'"{self.loaded_hash}"'},
        )
        self.assertEqual(resp.status_code, status.HTTP_412_PRECONDITION_FAILED)
        self.assertEqual(resp.data["content_hash"], self.file.content_hash)
        self.assertEqual(self._content(), b"somebody else's paragraphs")

    def test_stale_put_is_refused_too(self):
        self._move_the_blob()
        resp = self.client.put(
            self._url(),
            {
                "name": "note.md",
                "node_type": File.NodeType.FILE,
                "content": SimpleUploadedFile("note.md", b"clobbered"),
                "base_hash": self.loaded_hash,
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_412_PRECONDITION_FAILED)
        self.assertEqual(self._content(), b"somebody else's paragraphs")

    def test_a_save_without_a_precondition_is_unaffected(self):
        """Uploads, WebDAV and API scripts send no hash and keep working."""
        self._move_the_blob()
        resp = self._save(b"last write wins")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self._content(), b"last write wins")


class GroupFileEventTests(TestCase):
    """A file in a group folder reaches its group members through the group.

    They hold no ``FileShare`` row, so a fan-out built from shares alone never
    told them the lock was released and their editor stayed read-only.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username="grpowner",
            email="grpowner@test.com",
            password="pass123",
        )
        self.member = User.objects.create_user(
            username="grpmember",
            email="grpmember@test.com",
            password="pass123",
        )
        self.stranger = User.objects.create_user(
            username="grpstranger",
            email="grpstranger@test.com",
            password="pass123",
        )
        self.group = Group.objects.create(name="Team")
        self.owner.groups.add(self.group)
        self.member.groups.add(self.group)
        self.file = File.objects.create(
            owner=self.owner,
            name="shared.md",
            node_type=File.NodeType.FILE,
            group=self.group,
        )
        # LocMemCache is process-global and user pks repeat across rolled-back
        # test cases, so a mailbox filled by an earlier test can land in this
        # one's assertions.
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_group_members_receive_the_event(self):
        push_file_event(
            self.file,
            "lock_released",
            self.owner.username,
            exclude_user_id=self.owner.pk,
        )
        self.assertEqual(
            [e["type"] for e in drain_user_events("files", self.member.pk)],
            ["lock_released"],
        )
        self.assertEqual(drain_user_events("files", self.stranger.pk), [])
        self.assertEqual(drain_user_events("files", self.owner.pk), [])

    def test_release_notifies_group_members(self):
        """End to end: the DELETE on the lock endpoint reaches the group."""
        client = APIClient()
        client.force_authenticate(self.owner)
        client.post(f"/api/v1/files/{self.file.uuid}/lock")
        resp = client.delete(f"/api/v1/files/{self.file.uuid}/lock")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(
            [e["type"] for e in drain_user_events("files", self.member.pk)],
            ["lock_released"],
        )


class WriteGuardScopeTests(APITestCase):
    """The write guards must not answer for a file the caller cannot reach.

    Both answers are built from the row itself - the holder's username on a
    423, the stored hash on a 412 - so an unscoped lookup turns a write
    endpoint into a probe: knowing a UUID would be enough to learn who is
    editing a stranger's file, or how its content has changed over time.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username="scopeowner",
            email="scopeowner@test.com",
            password="pass123",
        )
        self.holder = User.objects.create_user(
            username="scopeholder",
            email="scopeholder@test.com",
            password="pass123",
        )
        self.outsider = User.objects.create_user(
            username="scopeoutsider",
            email="scopeoutsider@test.com",
            password="pass123",
        )
        self.file = FileService.create_file(
            self.owner,
            "secret.md",
            content=ContentFile(b"private", name="secret.md"),
            mime_type="text/markdown",
        )
        self.client.force_authenticate(self.outsider)

    def _url(self):
        return f"/api/v1/files/{self.file.uuid}"

    def test_outsider_cannot_probe_the_lock_holder(self):
        now = timezone.now()
        File.objects.filter(pk=self.file.pk).update(
            locked_by=self.holder,
            locked_at=now,
            lock_expires_at=now + timedelta(minutes=5),
        )
        resp = self.client.patch(self._url(), {"name": "renamed.md"})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn("scopeholder", str(resp.data))

    def test_outsider_cannot_probe_the_content_hash(self):
        resp = self.client.patch(
            self._url(),
            {
                "content": SimpleUploadedFile("secret.md", b"clobbered"),
                "base_hash": "not-the-stored-hash",
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn(self.file.content_hash, str(resp.data))

    def test_a_reachable_file_still_answers_423(self):
        """Scoping the lookup must not cost a collaborator their lock conflict."""
        FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with=self.outsider,
            permission=FileShare.Permission.READ_WRITE,
        )
        now = timezone.now()
        File.objects.filter(pk=self.file.pk).update(
            locked_by=self.holder,
            locked_at=now,
            lock_expires_at=now + timedelta(minutes=5),
        )
        resp = self.client.patch(
            self._url(),
            {"content": SimpleUploadedFile("secret.md", b"clobbered")},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_423_LOCKED)


class ConcurrentWriteTests(APITestCase):
    """Two writers holding the same precondition: exactly one may win.

    Checking the stored hash before the write cannot settle this on its own -
    both callers read the same row and both conclude they are current. The
    conditional UPDATE in ``FileService.update_content`` is what decides it,
    so these tests drive a competing write into the window between the two.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="racer",
            email="racer@test.com",
            password="pass123",
        )
        self.rival = User.objects.create_user(
            username="rival",
            email="rival@test.com",
            password="pass123",
        )
        self.file = FileService.create_file(
            self.user,
            "race.md",
            content=ContentFile(b"first", name="race.md"),
            mime_type="text/markdown",
        )
        self.loaded_hash = self.file.content_hash
        self.client.force_authenticate(self.user)

    def _url(self):
        return f"/api/v1/files/{self.file.uuid}"

    def _content(self):
        self.file.refresh_from_db()
        with self.file.content.open("rb") as handle:
            return handle.read()

    def _save(self, body, **extra):
        return self.client.patch(
            self._url(),
            {"content": SimpleUploadedFile("race.md", body), **extra},
            format="multipart",
        )

    def _during_the_write(self, competitor):
        """Run *competitor* once, after the guards passed and before the claim.

        ``detect_from_stream`` is the first thing ``update_context`` touches
        once the viewset has decided the precondition still holds, which puts
        it exactly in the window the claim exists to close.
        """
        from workspace.files.services import detection

        real = detection.detect_from_stream
        fired = []

        def inject(stream):
            if not fired:
                fired.append(True)
                competitor()
            return real(stream)

        return patch(
            "workspace.files.services.detection.detect_from_stream", inject
        ), fired

    def test_the_second_writer_is_refused(self):
        def rival_saves():
            FileService.update_content(
                File.objects.get(pk=self.file.pk),
                ContentFile(b"the rival's paragraphs", name="race.md"),
                name="race.md",
                acting_user=self.rival,
            )

        injector, fired = self._during_the_write(rival_saves)
        with injector:
            resp = self._save(b"mine", **{"base_hash": self.loaded_hash})

        self.assertTrue(fired, "the competing write never ran")
        self.assertEqual(resp.status_code, status.HTTP_412_PRECONDITION_FAILED)
        self.assertNotEqual(self._content(), b"mine")
        # The answer carries the version that was current when the claim ran,
        # so the client can resynchronise without a second round trip.
        self.assertNotEqual(resp.data["content_hash"], self.loaded_hash)

    def test_a_lock_taken_in_the_same_window_is_caught(self):
        """The claim carries the lock predicate, not just the hash."""

        def rival_locks():
            now = timezone.now()
            File.objects.filter(pk=self.file.pk).update(
                locked_by=self.rival,
                locked_at=now,
                lock_expires_at=now + timedelta(minutes=5),
            )

        injector, fired = self._during_the_write(rival_locks)
        with injector:
            resp = self._save(b"mine", **{"base_hash": self.loaded_hash})

        self.assertTrue(fired, "the competing lock never ran")
        self.assertEqual(resp.status_code, status.HTTP_412_PRECONDITION_FAILED)
        self.assertEqual(self._content(), b"first")

    def test_a_competing_write_that_changes_nothing_lets_us_through(self):
        """The claim keys on the version, not on "did anyone touch this row".

        A competing write that stores the bytes the caller started from leaves
        the hash where it was, so the caller's expectation is still accurate
        and there is nothing of the other write left to lose.
        """

        def rival_rewrites_the_same_bytes():
            FileService.update_content(
                File.objects.get(pk=self.file.pk),
                ContentFile(b"first", name="race.md"),
                name="race.md",
                acting_user=self.rival,
            )

        injector, fired = self._during_the_write(rival_rewrites_the_same_bytes)
        with injector:
            resp = self._save(b"mine", **{"base_hash": self.loaded_hash})

        self.assertTrue(fired, "the competing write never ran")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self._content(), b"mine")

    def test_a_write_without_a_precondition_is_never_claimed(self):
        """Uploads and WebDAV keep last-write-wins - no 412, no surprise."""

        def rival_saves():
            FileService.update_content(
                File.objects.get(pk=self.file.pk),
                ContentFile(b"the rival's paragraphs", name="race.md"),
                name="race.md",
                acting_user=self.rival,
            )

        injector, _ = self._during_the_write(rival_saves)
        with injector:
            resp = self._save(b"mine")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self._content(), b"mine")
