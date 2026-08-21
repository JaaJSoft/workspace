"""Hard-deleting a File row must remove its data on disk.

Regression: the pre_delete signal rebuilt folder paths by hand using the
pre-migration-0022 layout (``files/<username>/...`` instead of
``files/users/<username>/...``), so purged folders survived on disk and were
resurrected in the DB by the next filesystem sync.
"""

import os
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.sync import FileSyncService

User = get_user_model()


class HardDeleteStorageTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.user = User.objects.create_user(
            username="deluser",
            email="del@test.com",
            password="pass",
        )

    def tearDown(self):
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _user_root(self):
        return os.path.join(self.media_root, "files", "users", self.user.username)

    def test_hard_delete_file_removes_blob(self):
        with self.settings(MEDIA_ROOT=self.media_root):
            f = FileService.create_file(
                self.user,
                "doc.txt",
                content=ContentFile(b"bytes", name="doc.txt"),
            )
            blob = os.path.join(self.media_root, f.content.name)
            self.assertTrue(os.path.isfile(blob))

            FileService.soft_delete(f, acting_user=self.user)
            FileService.hard_delete(f, acting_user=self.user)

            self.assertFalse(os.path.exists(blob))

    def test_purge_trashed_folder_removes_directory(self):
        with self.settings(MEDIA_ROOT=self.media_root):
            folder = FileService.create_folder(self.user, "Docs")
            folder_dir = os.path.join(self._user_root(), "Docs")
            self.assertTrue(os.path.isdir(folder_dir))

            FileService.soft_delete(folder, acting_user=self.user)
            FileService.hard_delete(folder, acting_user=self.user)

            self.assertFalse(os.path.exists(folder_dir))

    def test_purge_nested_trashed_folder_removes_directory(self):
        # The old signal stripped the first segment of ``path``, so a nested
        # folder pointed at the wrong directory even relative to its base.
        with self.settings(MEDIA_ROOT=self.media_root):
            parent = FileService.create_folder(self.user, "Docs")
            sub = FileService.create_folder(self.user, "Sub", parent)
            sub_dir = os.path.join(self._user_root(), "Docs", "Sub")
            self.assertTrue(os.path.isdir(sub_dir))

            FileService.soft_delete(sub, acting_user=self.user)
            FileService.hard_delete(sub, acting_user=self.user)

            self.assertFalse(os.path.exists(sub_dir))
            self.assertTrue(os.path.isdir(os.path.join(self._user_root(), "Docs")))

    def test_purged_folder_does_not_reappear_after_sync(self):
        # The user-visible symptom: purge the folder, hit refresh, it's back.
        with self.settings(MEDIA_ROOT=self.media_root):
            folder = FileService.create_folder(self.user, "Ghost")
            FileService.soft_delete(folder, acting_user=self.user)
            FileService.hard_delete(folder, acting_user=self.user)

            FileSyncService().sync_user_recursive(self.user)

            self.assertFalse(
                File.objects.filter(owner=self.user, name="Ghost").exists()
            )
