"""Content hashing: computed on every write path, drives duplicate detection."""

import hashlib
import os
import shutil
import tempfile
from io import BytesIO, StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services.content_hash import (
    find_duplicates,
    hash_storage_file,
    hash_stream,
)
from workspace.files.sync import FileSyncService
from workspace.files.webdav.resources import FileResource

from .test_webdav import _make_environ

User = get_user_model()

HELLO = b"hello world"
HELLO_SHA256 = hashlib.sha256(HELLO).hexdigest()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


class HashStreamTests(TestCase):
    def test_hashes_whole_django_file_and_restores_position(self):
        stream = ContentFile(HELLO, name="hello.txt")
        stream.seek(3)
        self.assertEqual(hash_stream(stream), HELLO_SHA256)
        self.assertEqual(stream.tell(), 3)

    def test_hashes_raw_stream_from_the_start(self):
        stream = BytesIO(HELLO)
        stream.seek(5)
        self.assertEqual(hash_stream(stream), HELLO_SHA256)
        self.assertEqual(stream.tell(), 5)

    def test_hash_storage_file(self):
        path = default_storage.save("hash-test/hello.txt", ContentFile(HELLO))
        try:
            self.assertEqual(hash_storage_file(default_storage, path), HELLO_SHA256)
        finally:
            default_storage.delete(path)


class WritePathHashTests(TestCase):
    """Every path that writes bytes into a File row leaves the matching hash."""

    def setUp(self):
        self.user = User.objects.create_user(username="hasher", password="pass")

    def test_create_file_hashes_uploaded_content(self):
        f = FileService.create_file(
            self.user, "hello.txt", content=ContentFile(HELLO, name="hello.txt")
        )
        self.assertEqual(f.content_hash, HELLO_SHA256)
        # The blob itself was still written in full.
        with f.content.open("rb") as fh:
            self.assertEqual(fh.read(), HELLO)

    def test_create_file_without_content_has_no_hash(self):
        f = FileService.create_file(self.user, "empty.txt")
        self.assertEqual(f.content_hash, "")

    def test_folder_has_no_hash(self):
        folder = FileService.create_folder(self.user, "Docs")
        self.assertEqual(folder.content_hash, "")

    def test_update_content_refreshes_hash(self):
        f = FileService.create_file(
            self.user, "note.txt", content=ContentFile(HELLO, name="note.txt")
        )
        FileService.update_content(
            f, ContentFile(b"edited", name="note.txt"), acting_user=self.user
        )
        f.refresh_from_db()
        self.assertEqual(f.content_hash, _sha256(b"edited"))

    def test_replace_content_storage_stores_the_streamed_hash(self):
        f = FileService.create_file(
            self.user, "note.txt", content=ContentFile(HELLO, name="note.txt")
        )
        FileService.replace_content_storage(
            f,
            storage_path=f.content.name,
            size=6,
            content_hash=_sha256(b"edited"),
            acting_user=self.user,
        )
        f.refresh_from_db()
        self.assertEqual(f.content_hash, _sha256(b"edited"))

    def test_copy_carries_the_source_hash(self):
        f = FileService.create_file(
            self.user, "note.txt", content=ContentFile(HELLO, name="note.txt")
        )
        folder = FileService.create_folder(self.user, "Copies")
        copied = FileService.copy(f, folder, self.user, acting_user=self.user)
        self.assertEqual(copied.content_hash, HELLO_SHA256)

    def test_copy_of_a_legacy_row_computes_the_hash(self):
        f = FileService.create_file(
            self.user, "note.txt", content=ContentFile(HELLO, name="note.txt")
        )
        File.objects.filter(pk=f.pk).update(content_hash="")
        f.refresh_from_db()
        folder = FileService.create_folder(self.user, "Copies")
        copied = FileService.copy(f, folder, self.user, acting_user=self.user)
        self.assertEqual(copied.content_hash, HELLO_SHA256)

    def test_register_disk_file_hashes_the_blob(self):
        path = default_storage.save("hash-test/on-disk.txt", ContentFile(HELLO))
        try:
            f = FileService.register_disk_file(
                self.user, "on-disk.txt", None, path, size=len(HELLO)
            )
            self.assertEqual(f.content_hash, HELLO_SHA256)
        finally:
            default_storage.delete(path)

    def test_register_disk_file_survives_a_missing_blob(self):
        f = FileService.register_disk_file(
            self.user, "ghost.txt", None, "hash-test/ghost.txt", size=None
        )
        self.assertEqual(f.content_hash, "")


class WebDavHashTests(TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._media_override = override_settings(MEDIA_ROOT=self._tmpdir)
        self._media_override.enable()
        self.user = User.objects.create_user(username="davhash", password="pass")
        self.file = FileService.create_file(
            self.user, "test.txt", content=ContentFile(HELLO, name="test.txt")
        )
        self.res = FileResource("/test.txt", _make_environ(user=self.user), self.file)

    def tearDown(self):
        self._media_override.disable()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_put_hashes_in_the_write_pass(self):
        buf = self.res.begin_write(content_type="text/plain")
        buf.write(b"new ")
        buf.write(b"content")
        buf.close()
        self.res.end_write(with_errors=False)

        self.file.refresh_from_db()
        self.assertEqual(self.file.content_hash, _sha256(b"new content"))


class SyncHashTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.user = User.objects.create_user(username="synchash", password="pass")

    def tearDown(self):
        shutil.rmtree(self.media_root, ignore_errors=True)

    def test_disk_synced_files_are_hashed(self):
        with self.settings(MEDIA_ROOT=self.media_root):
            root = os.path.join(self.media_root, "files", "users", self.user.username)
            os.makedirs(root, exist_ok=True)
            with open(os.path.join(root, "found.txt"), "wb") as fh:
                fh.write(HELLO)

            FileSyncService().sync_user_recursive(self.user)

            f = File.objects.get(owner=self.user, name="found.txt")
            self.assertEqual(f.content_hash, HELLO_SHA256)


class FindDuplicatesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.other = User.objects.create_user(username="bob", password="pass")
        self.group = Group.objects.create(name="Marketing")
        self.user.groups.add(self.group)
        self.other.groups.add(self.group)
        self.group_root = FileService.create_folder(
            self.user, "Marketing Files", group=self.group
        )

    def _upload(self, owner, name, data=HELLO, parent=None):
        return FileService.create_file(
            owner, name, parent=parent, content=ContentFile(data, name=name)
        )

    def test_finds_own_personal_file_with_same_content(self):
        folder = FileService.create_folder(self.user, "Docs")
        existing = self._upload(self.user, "a.txt", parent=folder)
        new = self._upload(self.user, "b.txt")
        self.assertEqual(list(find_duplicates(new)), [existing])

    def test_excludes_itself_and_different_content(self):
        self._upload(self.user, "other.txt", data=b"different")
        new = self._upload(self.user, "b.txt")
        self.assertEqual(list(find_duplicates(new)), [])

    def test_never_reveals_another_users_file(self):
        self._upload(self.other, "secret.txt")
        new = self._upload(self.user, "b.txt")
        self.assertEqual(list(find_duplicates(new)), [])

    def test_ignores_trashed_files(self):
        existing = self._upload(self.user, "a.txt")
        FileService.soft_delete(existing, acting_user=self.user)
        new = self._upload(self.user, "b.txt")
        self.assertEqual(list(find_duplicates(new)), [])

    def test_group_upload_matches_the_same_group_only(self):
        # A personal file and another group's file don't count...
        self._upload(self.user, "personal.txt")
        other_group = Group.objects.create(name="Sales")
        self.user.groups.add(other_group)
        other_root = FileService.create_folder(
            self.user, "Sales Files", group=other_group
        )
        self._upload(self.user, "sales.txt", parent=other_root)
        # ...but a teammate's file in the same group folder does.
        teammate = self._upload(self.other, "shared.txt", parent=self.group_root)

        new = self._upload(self.user, "new.txt", parent=self.group_root)
        self.assertEqual(list(find_duplicates(new)), [teammate])

    def test_personal_upload_ignores_group_files(self):
        self._upload(self.user, "shared.txt", parent=self.group_root)
        new = self._upload(self.user, "new.txt")
        self.assertEqual(list(find_duplicates(new)), [])

    def test_row_without_hash_has_no_duplicates(self):
        self._upload(self.user, "a.txt")
        new = FileService.create_file(self.user, "empty.txt")
        self.assertEqual(list(find_duplicates(new)), [])

    def test_same_hash_but_different_size_is_not_a_duplicate(self):
        # A hash collision, or a hash left stale by a blob rewrite, must not
        # report a file of another size as the same content.
        existing = self._upload(self.user, "a.txt")
        File.objects.filter(pk=existing.pk).update(size=existing.size + 1)
        new = self._upload(self.user, "b.txt")
        self.assertEqual(list(find_duplicates(new)), [])


class UploadDuplicateApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.other = User.objects.create_user(username="bob", password="pass")
        self.client.force_authenticate(user=self.user)

    def _post(self, name, data=HELLO, parent=None):
        payload = {
            "name": name,
            "node_type": "file",
            "content": SimpleUploadedFile(name, data),
        }
        if parent is not None:
            payload["parent"] = str(parent.uuid)
        return self.client.post("/api/v1/files", payload, format="multipart")

    def test_first_upload_reports_no_duplicates(self):
        response = self._post("a.txt")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["content_hash"], HELLO_SHA256)
        self.assertEqual(response.data["duplicates"], [])

    def test_second_upload_of_identical_content_reports_where_it_lives(self):
        folder = FileService.create_folder(self.user, "Docs")
        first = self._post("a.txt", parent=folder)
        second = self._post("copy.txt")
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(
            second.data["duplicates"],
            [
                {
                    "uuid": first.data["uuid"],
                    "name": "a.txt",
                    "path": "Docs/a.txt",
                    "parent": str(folder.uuid),
                }
            ],
        )
        # Never deduplicated silently: both rows exist with their own blob.
        rows = File.objects.filter(owner=self.user, node_type=File.NodeType.FILE)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(len({r.content.name for r in rows}), 2)

    def test_another_users_identical_file_is_not_revealed(self):
        FileService.create_file(
            self.other, "secret.txt", content=ContentFile(HELLO, name="secret.txt")
        )
        response = self._post("a.txt")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["duplicates"], [])

    def test_content_update_refreshes_hash(self):
        f = FileService.create_file(
            self.user, "note.txt", content=ContentFile(HELLO, name="note.txt")
        )
        response = self.client.patch(
            f"/api/v1/files/{f.uuid}",
            {"content": SimpleUploadedFile("note.txt", b"edited")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.content)
        f.refresh_from_db()
        self.assertEqual(f.content_hash, _sha256(b"edited"))

    def test_clearing_content_clears_the_hash(self):
        f = FileService.create_file(
            self.user, "note.txt", content=ContentFile(HELLO, name="note.txt")
        )
        response = self.client.patch(
            f"/api/v1/files/{f.uuid}", {"content": None}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        f.refresh_from_db()
        self.assertFalse(f.content)
        self.assertEqual(f.content_hash, "")

    def test_folder_creation_has_no_duplicates_key(self):
        response = self.client.post(
            "/api/v1/files", {"name": "Docs", "node_type": "folder"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("duplicates", response.data)


class BackfillFileHashesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="backfill", password="pass")

    def _legacy_file(self, name, data):
        f = FileService.create_file(
            self.user, name, content=ContentFile(data, name=name)
        )
        File.objects.filter(pk=f.pk).update(content_hash="")
        f.refresh_from_db()
        return f

    def test_fills_missing_hashes_from_storage(self):
        f = self._legacy_file("old.txt", HELLO)
        out = StringIO()
        call_command("backfill_file_hashes", stdout=out)
        f.refresh_from_db()
        self.assertEqual(f.content_hash, HELLO_SHA256)
        self.assertIn("Updated: 1", out.getvalue())

    def test_leaves_existing_hashes_and_folders_alone(self):
        f = FileService.create_file(
            self.user, "new.txt", content=ContentFile(b"x", name="new.txt")
        )
        File.objects.filter(pk=f.pk).update(content_hash="f" * 64)
        FileService.create_folder(self.user, "Docs")
        call_command("backfill_file_hashes", stdout=StringIO())
        f.refresh_from_db()
        self.assertEqual(f.content_hash, "f" * 64)

    def test_dry_run_writes_nothing(self):
        f = self._legacy_file("old.txt", HELLO)
        out = StringIO()
        call_command("backfill_file_hashes", "--dry-run", stdout=out)
        f.refresh_from_db()
        self.assertEqual(f.content_hash, "")
        self.assertIn("Would update: 1", out.getvalue())

    def test_rejects_batch_size_below_one(self):
        for value in ("0", "-1"):
            with self.assertRaises(CommandError):
                call_command("backfill_file_hashes", "--batch-size", value)

    def test_rejects_negative_limit_and_honours_zero(self):
        f = self._legacy_file("old.txt", HELLO)
        with self.assertRaises(CommandError):
            call_command("backfill_file_hashes", "--limit", "-1")
        call_command("backfill_file_hashes", "--limit", "0", stdout=StringIO())
        f.refresh_from_db()
        self.assertEqual(f.content_hash, "")
        call_command("backfill_file_hashes", "--limit", "1", stdout=StringIO())
        f.refresh_from_db()
        self.assertEqual(f.content_hash, HELLO_SHA256)

    def test_blob_vanishing_after_the_existence_check_counts_as_missing(self):
        self._legacy_file("gone.txt", HELLO)
        later = self._legacy_file("still-there.txt", b"still")
        with patch(
            "workspace.files.management.commands.backfill_file_hashes.hash_storage_file",
            side_effect=[FileNotFoundError("gone"), _sha256(b"still")],
        ):
            out = StringIO()
            call_command("backfill_file_hashes", stdout=out)
        later.refresh_from_db()
        self.assertEqual(later.content_hash, _sha256(b"still"))
        self.assertIn("Updated: 1, missing: 1", out.getvalue())

    def test_reads_candidates_in_closed_pages_never_across_a_flush(self):
        # A streaming iterator keeps a read cursor open while _flush() opens
        # a write transaction; on SQLite (WAL, BEGIN IMMEDIATE) that raises
        # "database is locked" as soon as the app commits meanwhile. Pin the
        # page-by-page shape: every candidate SELECT is bounded to the batch
        # size and there are as many of them as pages, so no cursor outlives
        # the page it belongs to.
        for i in range(5):
            self._legacy_file(f"f{i}.txt", f"data-{i}".encode())
        with CaptureQueriesContext(connection) as ctx:
            call_command("backfill_file_hashes", "--batch-size", "2", stdout=StringIO())
        candidate_selects = [
            q["sql"]
            for q in ctx.captured_queries
            if q["sql"].startswith("SELECT") and "content_hash" in q["sql"]
        ]
        # 5 rows in pages of 2: three full/partial pages plus the empty tail.
        self.assertEqual(len(candidate_selects), 4)
        self.assertTrue(all("LIMIT 2" in sql for sql in candidate_selects))
        self.assertEqual(File.objects.filter(content_hash="").count(), 0)

    def test_does_not_overwrite_a_hash_written_meanwhile(self):
        f = self._legacy_file("old.txt", HELLO)
        original = File.objects.filter

        def rewrite_then_filter(*args, **kwargs):
            # A concurrent content write lands between hashing and the flush.
            if kwargs.get("content_hash") == "" and "pk" in kwargs:
                original(pk=f.pk).update(content_hash=_sha256(b"fresh"))
                File.objects.filter = original
            return original(*args, **kwargs)

        out = StringIO()
        with patch.object(File.objects, "filter", side_effect=rewrite_then_filter):
            call_command("backfill_file_hashes", stdout=out)
        f.refresh_from_db()
        self.assertEqual(f.content_hash, _sha256(b"fresh"))
        self.assertIn("Updated: 0", out.getvalue())

    def test_counts_missing_blobs(self):
        f = self._legacy_file("gone.txt", HELLO)
        f.content.storage.delete(f.content.name)
        out = StringIO()
        call_command("backfill_file_hashes", stdout=out)
        f.refresh_from_db()
        self.assertEqual(f.content_hash, "")
        self.assertIn("missing: 1", out.getvalue())
