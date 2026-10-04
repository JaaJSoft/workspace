"""Hard-deleting a File row must remove its data on disk.

Regression: the pre_delete signal rebuilt folder paths by hand using the
pre-migration-0022 layout (``files/<username>/...`` instead of
``files/users/<username>/...``), so purged folders survived on disk and were
resurrected in the DB by the next filesystem sync.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.sync import FileSyncService

User = get_user_model()


class HardDeleteStorageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="deluser",
            email="del@test.com",
            password="pass",
        )

    def _user_root(self):
        return f"files/users/{self.user.username}"

    def _purge(self, node):
        # Object storage drops what trashing copied once the transaction commits.
        with self.captureOnCommitCallbacks(execute=True):
            FileService.soft_delete(node, acting_user=self.user)
        with self.captureOnCommitCallbacks(execute=True):
            FileService.hard_delete(node, acting_user=self.user)

    def test_hard_delete_file_removes_blob(self):
        f = FileService.create_file(
            self.user,
            "doc.txt",
            content=ContentFile(b"bytes", name="doc.txt"),
        )
        blob = f.content.name
        self.assertTrue(default_storage.is_file(blob))

        self._purge(f)

        self.assertFalse(default_storage.exists(blob))

    def test_purge_trashed_folder_removes_directory(self):
        folder = FileService.create_folder(self.user, "Docs")
        folder_dir = f"{self._user_root()}/Docs"
        self.assertTrue(default_storage.is_dir(folder_dir))

        self._purge(folder)

        self.assertFalse(default_storage.exists(folder_dir))

    def test_purge_nested_trashed_folder_removes_directory(self):
        # The old signal stripped the first segment of ``path``, so a nested
        # folder pointed at the wrong directory even relative to its base.
        parent = FileService.create_folder(self.user, "Docs")
        sub = FileService.create_folder(self.user, "Sub", parent)
        sub_dir = f"{self._user_root()}/Docs/Sub"
        self.assertTrue(default_storage.is_dir(sub_dir))

        self._purge(sub)

        self.assertFalse(default_storage.exists(sub_dir))
        self.assertTrue(default_storage.is_dir(f"{self._user_root()}/Docs"))

    def test_purge_group_folder_removes_directory(self):
        group = Group.objects.create(name="Team")
        folder = FileService.create_folder(self.user, "Team", group=group)
        group_dir = "files/groups/Team"
        self.assertTrue(default_storage.is_dir(group_dir))

        self._purge(folder)

        self.assertFalse(default_storage.exists(group_dir))

    def test_dot_names_are_rejected_at_save(self):
        for name in (".", ".."):
            with self.assertRaises(ValueError):
                FileService.create_folder(self.user, name)

    def test_purge_refuses_dot_dot_path_components(self):
        # A row named '.' or '..' (legacy data, hand-edited DB) resolves to an
        # ancestor directory; the cleanup must fail closed instead of running
        # rmtree there and taking unrelated data with it.
        keep = FileService.create_folder(self.user, "Keep")
        keep_dir = f"{self._user_root()}/Keep"

        for evil_name in (".", ".."):
            folder = FileService.create_folder(self.user, "Evil")
            # Bypass File.save() validation, as corrupt data would.
            File.objects.filter(pk=folder.pk).update(name=evil_name, path=evil_name)
            folder.refresh_from_db()

            FileService.hard_delete(folder, acting_user=self.user)

            self.assertTrue(default_storage.is_dir(keep_dir))
            self.assertTrue(default_storage.is_dir(self._user_root()))
        self.assertTrue(File.objects.filter(pk=keep.pk).exists())

    def test_purged_folder_does_not_reappear_after_sync(self):
        # The user-visible symptom: purge the folder, hit refresh, it's back.
        folder = FileService.create_folder(self.user, "Ghost")
        self._purge(folder)

        FileSyncService().sync_user_recursive(self.user)

        self.assertFalse(File.objects.filter(owner=self.user, name="Ghost").exists())
