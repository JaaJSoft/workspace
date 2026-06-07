"""Tests for FileSyncService disk <-> DB synchronization."""

import os
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.files.models import File
from workspace.files.sync import FileSyncService

User = get_user_model()


@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.FileSystemStorage")
class FileSyncServiceStoragePrefixTests(TestCase):
    """Verify the sync service uses the canonical ``files/users/<username>/`` prefix.

    The personal-files storage layout is owned by ``File.upload_to`` (see
    ``workspace/files/models.py``) and was normalized by migration 0022. Sync
    must read from and register paths under the same root, otherwise it would
    treat every existing file as missing on disk and soft-delete the lot.
    """

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.user = User.objects.create_user(
            username="syncuser",
            email="sync@test.com",
            password="pass",
        )

    def tearDown(self):
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _user_root(self):
        return os.path.join(self.media_root, "files", "users", self.user.username)

    def _write(self, *parts, contents=b"data"):
        full = os.path.join(self._user_root(), *parts)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(contents)
        return full

    def test_recursive_sync_registers_files_under_canonical_prefix(self):
        with self.settings(MEDIA_ROOT=self.media_root):
            self._write("report.pdf", contents=b"%PDF-1.4 test")

            FileSyncService().sync_user_recursive(self.user)

            f = File.objects.get(owner=self.user, name="report.pdf")
            self.assertEqual(
                f.content.name,
                f"files/users/{self.user.username}/report.pdf",
            )

    def test_shallow_sync_at_root_registers_under_canonical_prefix(self):
        with self.settings(MEDIA_ROOT=self.media_root):
            self._write("notes.txt", contents=b"hello")

            FileSyncService().sync_folder_shallow(self.user, parent_db=None)

            f = File.objects.get(owner=self.user, name="notes.txt")
            self.assertEqual(
                f.content.name,
                f"files/users/{self.user.username}/notes.txt",
            )

    def test_shallow_sync_inside_subfolder_registers_under_canonical_prefix(self):
        with self.settings(MEDIA_ROOT=self.media_root):
            # Folder must exist in DB for shallow sync to scan its disk path.
            from workspace.files.services import FileService

            sub = FileService.create_folder(self.user, "Sub")

            self._write("Sub", "inside.md", contents=b"# hi")

            FileSyncService().sync_folder_shallow(self.user, parent_db=sub)

            f = File.objects.get(owner=self.user, name="inside.md")
            self.assertEqual(
                f.content.name,
                f"files/users/{self.user.username}/Sub/inside.md",
            )

    def test_recursive_sync_does_not_soft_delete_existing_db_records(self):
        # Regression: with the wrong prefix, sync looked at files/<user>/ which
        # is empty, so every DB record under files/users/<user>/ was treated as
        # missing-on-disk and soft-deleted in phase 2.
        with self.settings(MEDIA_ROOT=self.media_root):
            from django.core.files.base import ContentFile

            from workspace.files.services import FileService

            f = FileService.create_file(
                self.user,
                "keep.txt",
                content=ContentFile(b"keep me", name="keep.txt"),
            )

            FileSyncService().sync_user_recursive(self.user)

            f.refresh_from_db()
            self.assertIsNone(f.deleted_at)
