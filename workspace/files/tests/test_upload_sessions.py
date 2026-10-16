"""Resumable uploads: a file sent in chunks, stored once all of it arrived."""

import hashlib
import os
import shutil
import tempfile
import uuid
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files import locks
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.files.models import File, UploadSession
from workspace.files.services import FileService, quota, uploads

User = get_user_model()

KB = 1024
DATA = bytes(range(256)) * 40  # 10 KB, every byte value
DATA_SIZE = len(DATA)
DATA_SHA256 = hashlib.sha256(DATA).hexdigest()

UPLOADS = "/api/v1/files/uploads"


def _read(file_obj):
    with file_obj.content.open("rb") as fh:
        return fh.read()


class UploadSessionApiMixin:
    def _open(self, name="video.mp4", size=DATA_SIZE, parent=None, **extra):
        payload = {"name": name, "size": size, **extra}
        if parent is not None:
            payload["parent"] = str(parent.uuid)
        return self.client.post(UPLOADS, payload, format="json")

    def _chunk(self, uuid, data, start, size=DATA_SIZE, content_range=None):
        end = start + len(data) - 1
        return self.client.generic(
            "PATCH",
            f"{UPLOADS}/{uuid}",
            data,
            content_type="application/octet-stream",
            HTTP_CONTENT_RANGE=content_range or f"bytes {start}-{end}/{size}",
        )

    def _send(self, uuid, data=DATA, chunk=4 * KB):
        for start in range(0, len(data), chunk):
            response = self._chunk(uuid, data[start : start + chunk], start, len(data))
            self.assertEqual(response.status_code, 200, response.content)
        return response

    def _finalize(self, uuid):
        return self.client.post(f"{UPLOADS}/{uuid}/finalize")

    def _upload(self, name="video.mp4", data=DATA, parent=None, **extra):
        opened = self._open(name, len(data), parent, **extra)
        self.assertEqual(opened.status_code, 201, opened.content)
        self._send(opened.data["uuid"], data)
        return self._finalize(opened.data["uuid"])


class OpenSessionTests(UploadSessionApiMixin, APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.other = User.objects.create_user(username="bob", password="pw")
        self.client.force_authenticate(self.user)

    def test_opening_answers_where_to_start_and_creates_the_spool(self):
        response = self._open()

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["offset"], 0)
        self.assertEqual(response.data["size"], len(DATA))
        self.assertEqual(response.data["chunk_size"], uploads.CHUNK_SIZE)
        session = UploadSession.objects.get(pk=response.data["uuid"])
        self.assertTrue(os.path.exists(uploads.spool_path(session)))

    def test_a_browser_session_can_open_one(self):
        # A logged-in browser hands the view a lazy user, not a User row.
        self.client.force_authenticate(None)
        self.client.force_login(self.user)

        response = self._open()

        self.assertEqual(response.status_code, 201, response.content)

    def test_a_folder_the_user_cannot_add_to_is_refused(self):
        theirs = FileService.create_folder(self.other, "Private")
        trashed = FileService.create_folder(self.user, "Old")
        FileService.soft_delete(trashed)
        a_file = FileService.create_file(
            self.user, "a.txt", content=ContentFile(b"a", name="a.txt")
        )

        for parent in (theirs, trashed, a_file):
            with self.subTest(parent=parent.name):
                response = self._open(parent=parent)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("parent", response.data)
        self.assertFalse(UploadSession.objects.exists())

    def test_a_group_folder_takes_uploads_from_its_members(self):
        group = Group.objects.create(name="Team")
        self.user.groups.add(group)
        root = FileService.create_folder(self.other, "Team", group=group)

        response = self._open(parent=root)

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(UploadSession.objects.get().group, group)

    def test_a_taken_name_is_refused_before_any_byte_is_sent(self):
        FileService.create_file(
            self.user, "video.mp4", content=ContentFile(b"v1", name="video.mp4")
        )

        response = self._open(name="VIDEO.mp4")

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("name", response.data)

    def test_a_taken_name_is_accepted_when_told_what_to_do_with_it(self):
        FileService.create_file(
            self.user, "video.mp4", content=ContentFile(b"v1", name="video.mp4")
        )

        for on_conflict in ("rename", "replace"):
            with self.subTest(on_conflict=on_conflict):
                response = self._open(on_conflict=on_conflict)
                self.assertEqual(response.status_code, 201, response.content)

    def test_a_name_with_a_separator_is_refused(self):
        response = self._open(name="../video.mp4")

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("name", response.data)

    def test_a_dot_name_is_refused_before_any_byte_is_sent(self):
        # The row would refuse it too, but only once the whole file is in.
        for name in (".", ".."):
            with self.subTest(name=name):
                response = self._open(name=name)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("name", response.data)
        self.assertFalse(UploadSession.objects.exists())


@override_settings(STORAGE_QUOTA_BYTES=100 * KB)
class ReservationTests(UploadSessionApiMixin, APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.client.force_authenticate(self.user)

    def test_an_open_retried_with_its_uuid_reserves_once(self):
        # The first answer was lost: the client asks again for the same session.
        session_uuid = str(uuid.uuid4())
        first = self._open("a.mp4", size=60 * KB, uuid=session_uuid)
        again = self._open("a.mp4", size=60 * KB, uuid=session_uuid)

        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.data["uuid"], session_uuid)
        self.assertEqual(UploadSession.objects.count(), 1)
        self.assertEqual(quota.reserved_bytes(owner=self.user, group=None), 60 * KB)

    def test_a_uuid_another_user_holds_is_refused(self):
        other = User.objects.create_user(username="bob", password="pw")
        theirs, _created = uploads.open_session(
            other,
            name="b.mp4",
            parent=None,
            size=10,
            on_conflict="error",
            uuid=uuid.uuid4(),
        )

        response = self._open("a.mp4", size=10, uuid=str(theirs.pk))

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("uuid", response.data)
        self.assertEqual(UploadSession.objects.get().owner, other)

    def test_two_sessions_cannot_together_overflow_the_quota(self):
        first = self._open("a.mp4", size=60 * KB)
        second = self._open("b.mp4", size=60 * KB)

        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 413, second.content)

    def test_other_writes_see_the_reservation(self):
        self._open("a.mp4", size=60 * KB)

        response = self.client.post(
            "/api/v1/files",
            {
                "name": "b.bin",
                "node_type": "file",
                "content": ContentFile(b"x" * 50 * KB, name="b.bin"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 413, response.content)

    def test_aborting_hands_the_reservation_back(self):
        first = self._open("a.mp4", size=60 * KB)
        self.client.delete(f"{UPLOADS}/{first.data['uuid']}")

        self.assertEqual(self._open("b.mp4", size=60 * KB).status_code, 201)

    def test_shown_usage_leaves_reservations_out(self):
        self._open("a.mp4", size=60 * KB)

        self.assertEqual(quota.personal_usage(self.user), 0)

    def test_finalizing_does_not_count_its_own_bytes_twice(self):
        data = b"x" * 80 * KB

        response = self._upload("a.mp4", data)

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(quota.personal_usage(self.user), 80 * KB)
        self.assertEqual(quota.reserved_bytes(owner=self.user, group=None), 0)


class AppendTests(UploadSessionApiMixin, APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.other = User.objects.create_user(username="bob", password="pw")
        self.client.force_authenticate(self.user)
        self.uuid = self._open().data["uuid"]

    def test_chunks_in_order_move_the_offset(self):
        response = self._chunk(self.uuid, DATA[: 4 * KB], 0)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data, {"offset": 4 * KB})
        self.assertEqual(
            self.client.get(f"{UPLOADS}/{self.uuid}").data["offset"], 4 * KB
        )

    def test_a_chunk_out_of_order_is_refused_with_where_to_resume(self):
        self._chunk(self.uuid, DATA[: 4 * KB], 0)

        ahead = self._chunk(self.uuid, DATA[8 * KB :], 8 * KB)
        again = self._chunk(self.uuid, DATA[: 4 * KB], 0)

        for response in (ahead, again):
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(response.data["offset"], 4 * KB)

    def test_a_malformed_or_oversized_range_is_refused(self):
        cases = {
            "no range": "",
            "not bytes": "items 0-9/10240",
            "other size": f"bytes 0-9/{len(DATA) + 1}",
            "past the end": f"bytes 10235-10244/{len(DATA)}",
        }
        for label, content_range in cases.items():
            with self.subTest(label):
                response = self._chunk(
                    self.uuid, b"x" * 10, 0, content_range=content_range or " "
                )
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(UploadSession.objects.get(pk=self.uuid).received, 0)

    def test_a_chunk_over_the_limit_is_refused(self):
        size = 4 * uploads.MAX_CHUNK_SIZE
        uuid = self._open("big.bin", size=size).data["uuid"]

        response = self.client.generic(
            "PATCH",
            f"{UPLOADS}/{uuid}",
            b"",
            content_type="application/octet-stream",
            HTTP_CONTENT_RANGE=f"bytes 0-{uploads.MAX_CHUNK_SIZE}/{size}",
        )

        self.assertEqual(response.status_code, 413, response.content)

    def test_a_chunk_is_refused_while_another_request_writes(self):
        session = UploadSession.objects.get(pk=self.uuid)
        with open(uploads._lock_path(session), "ab") as held:
            self.assertTrue(locks.lock(held, locks.LOCK_EX | locks.LOCK_NB))
            response = self._chunk(self.uuid, DATA[: 4 * KB], 0)
            locks.unlock(held)

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data["offset"], 0)

    def test_another_users_session_is_out_of_reach(self):
        self.client.force_authenticate(self.other)

        self.assertEqual(self.client.get(f"{UPLOADS}/{self.uuid}").status_code, 404)
        self.assertEqual(self._chunk(self.uuid, DATA[:10], 0).status_code, 404)
        self.assertEqual(self._finalize(self.uuid).status_code, 404)
        self.assertEqual(self.client.delete(f"{UPLOADS}/{self.uuid}").status_code, 404)
        self.assertTrue(UploadSession.objects.filter(pk=self.uuid).exists())


class AppendServiceTests(TestCase):
    """What the API cannot drive: cut bodies, other workers, dead requests."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.session, _created = uploads.open_session(
            self.user,
            name="video.mp4",
            parent=None,
            size=len(DATA),
            on_conflict="error",
        )

    def _finish(self):
        with uploads.finished_upload(self.session) as content:
            return FileService.create_file(
                self.user, "video.mp4", content=content, check_quota=False
            )

    def test_a_cut_chunk_keeps_what_arrived(self):
        with self.assertRaises(uploads.ChunkIncomplete) as cut:
            uploads.append(self.session, 0, BytesIO(DATA[:3000]), 4 * KB)

        self.assertEqual(cut.exception.offset, 3000)
        uploads.append(self.session, 3000, BytesIO(DATA[3000:]), len(DATA) - 3000)
        self.assertEqual(self._finish().content_hash, DATA_SHA256)

    def test_a_chunk_landing_on_another_worker_still_hashes_right(self):
        uploads.append(self.session, 0, BytesIO(DATA[:4000]), 4000)
        uploads._hashers.clear()  # the next chunk reaches a worker that saw none
        uploads.append(self.session, 4000, BytesIO(DATA[4000:]), len(DATA) - 4000)
        uploads._hashers.clear()

        stored = self._finish()

        self.assertEqual(stored.content_hash, DATA_SHA256)
        self.assertEqual(_read(stored), DATA)

    def test_bytes_a_dead_request_left_past_the_offset_are_overwritten(self):
        uploads.append(self.session, 0, BytesIO(DATA[:4000]), 4000)
        with open(uploads.spool_path(self.session), "ab") as spool:
            spool.write(b"\0" * 5000)

        uploads.append(self.session, 4000, BytesIO(DATA[4000:]), len(DATA) - 4000)

        stored = self._finish()
        self.assertEqual(_read(stored), DATA)
        self.assertEqual(stored.content_hash, DATA_SHA256)

    def test_finalizing_moves_the_spool_into_place(self):
        uploads.append(self.session, 0, BytesIO(DATA), len(DATA))
        spool = uploads.spool_path(self.session)
        inode = os.stat(spool).st_ino

        stored = self._finish()

        self.assertFalse(os.path.exists(spool))
        self.assertEqual(os.stat(stored.content.path).st_ino, inode)


class FinalizeTests(UploadSessionApiMixin, APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.client.force_authenticate(self.user)

    def test_the_file_is_stored_with_its_content_and_hash(self):
        folder = FileService.create_folder(self.user, "Videos")

        response = self._upload(parent=folder, mime_type="video/mp4")

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["name"], "video.mp4")
        self.assertEqual(response.data["parent"], folder.uuid)
        self.assertEqual(response.data["content_hash"], DATA_SHA256)
        self.assertEqual(response.data["duplicates"], [])
        stored = File.objects.get(pk=response.data["uuid"])
        self.assertEqual(_read(stored), DATA)
        self.assertEqual(stored.size, len(DATA))
        session = UploadSession.objects.get()
        self.assertIsNotNone(session.completed_at)
        self.assertFalse(os.path.exists(uploads.spool_path(session)))

    def test_finalizing_before_every_byte_arrived_says_what_is_missing(self):
        uuid = self._open().data["uuid"]
        self._chunk(uuid, DATA[: 4 * KB], 0)

        response = self._finalize(uuid)

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data["offset"], 4 * KB)
        self.assertFalse(File.objects.filter(node_type=File.NodeType.FILE).exists())

    def test_finalizing_again_answers_the_same_without_a_second_file(self):
        uuid = self._open().data["uuid"]
        self._send(uuid)
        first = self._finalize(uuid)

        again = self._finalize(uuid)

        self.assertEqual(again.status_code, 201, again.content)
        self.assertEqual(again.data["uuid"], first.data["uuid"])
        self.assertEqual(File.objects.filter(node_type=File.NodeType.FILE).count(), 1)

    def test_completion_is_recorded_before_a_retry_can_take_the_lock(self):
        uuid = self._open().data["uuid"]
        self._send(uuid)
        session = UploadSession.objects.get(pk=uuid)
        record = uploads.complete
        lock_was_free = []

        def recording(*args, **kwargs):
            with open(uploads._lock_path(session), "ab") as handle:
                taken = locks.lock(handle, locks.LOCK_EX | locks.LOCK_NB)
                if taken:
                    locks.unlock(handle)
            lock_was_free.append(taken)
            return record(*args, **kwargs)

        with patch.object(uploads, "complete", side_effect=recording):
            self.assertEqual(self._finalize(uuid).status_code, 201)

        self.assertEqual(lock_was_free, [False])

    def test_a_retry_that_read_the_session_before_it_completed_gets_the_file(self):
        uuid = self._open().data["uuid"]
        self._send(uuid)
        before = UploadSession.objects.get(pk=uuid)
        first = self._finalize(uuid)

        with patch(
            "workspace.files.views.uploads._session_or_none",
            side_effect=[before, UploadSession.objects.get(pk=uuid)],
        ):
            again = self._finalize(uuid)

        self.assertEqual(again.status_code, 201, again.content)
        self.assertEqual(again.data["uuid"], first.data["uuid"])

    def test_a_name_taken_during_the_transfer_is_refused_by_default(self):
        uuid = self._open().data["uuid"]
        self._send(uuid)
        FileService.create_file(
            self.user, "video.mp4", content=ContentFile(b"v1", name="video.mp4")
        )

        response = self._finalize(uuid)

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("name", response.data)

    def test_rename_keeps_both(self):
        FileService.create_file(
            self.user, "video.mp4", content=ContentFile(b"v1", name="video.mp4")
        )

        response = self._upload(on_conflict="rename")

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["name"], "video (Copy).mp4")

    def test_replace_writes_into_the_existing_file(self):
        existing = FileService.create_file(
            self.user, "video.mp4", content=ContentFile(b"v1", name="video.mp4")
        )

        response = self._upload(on_conflict="replace")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["uuid"], str(existing.uuid))
        existing.refresh_from_db()
        self.assertEqual(_read(existing), DATA)
        self.assertEqual(existing.content_hash, DATA_SHA256)

    def test_a_folder_trashed_during_the_transfer_is_refused(self):
        folder = FileService.create_folder(self.user, "Videos")
        uuid = self._open(parent=folder).data["uuid"]
        self._send(uuid)
        FileService.soft_delete(folder)

        response = self._finalize(uuid)

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("parent", response.data)

    def test_aborting_drops_the_session_and_its_bytes(self):
        uuid = self._open().data["uuid"]
        self._chunk(uuid, DATA[: 4 * KB], 0)
        spool = uploads.spool_path(UploadSession.objects.get(pk=uuid))

        response = self.client.delete(f"{UPLOADS}/{uuid}")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(UploadSession.objects.exists())
        self.assertFalse(os.path.exists(spool))
        self.assertEqual(self._chunk(uuid, DATA[4 * KB :], 4 * KB).status_code, 404)


class PurgeExpiredTests(TestCase):
    def setUp(self):
        self.spool_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.spool_dir, ignore_errors=True)
        override = override_settings(FILE_UPLOAD_TEMP_DIR=self.spool_dir)
        override.enable()
        self.addCleanup(override.disable)
        self.user = User.objects.create_user(username="alice", password="pw")

    def _session(self, name):
        session, _created = uploads.open_session(
            self.user, name=name, parent=None, size=10, on_conflict="error"
        )
        return session

    def test_an_abandoned_session_goes_with_its_spool(self):
        abandoned = self._session("old.mp4")
        live = self._session("new.mp4")
        UploadSession.objects.filter(pk=abandoned.pk).update(
            updated_at=timezone.now() - timedelta(days=2)
        )

        removed = uploads.purge_expired(timezone.now() - timedelta(days=1))

        self.assertEqual(removed, 1)
        self.assertEqual(list(UploadSession.objects.all()), [live])
        self.assertFalse(os.path.exists(uploads.spool_path(abandoned)))
        self.assertTrue(os.path.exists(uploads.spool_path(live)))

    def test_a_spool_whose_session_went_with_its_owner_goes_too(self):
        orphan = self._session("orphan.mp4")
        path = uploads.spool_path(orphan)
        self.user.delete()
        stamp = (timezone.now() - timedelta(days=2)).timestamp()
        os.utime(path, (stamp, stamp))

        self.assertEqual(uploads.purge_expired(timezone.now() - timedelta(days=1)), 1)
        self.assertFalse(os.path.exists(path))
