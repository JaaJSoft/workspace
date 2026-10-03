"""Tests for FileSyncService disk <-> DB synchronization."""

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.common.tests.media import IsolatedMediaRootMixin
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.sync import FileSyncService

User = get_user_model()


class FileSyncServiceStoragePrefixTests(TestCase):
    """Verify the sync service uses the canonical ``files/users/<username>/`` prefix.

    The personal-files storage layout is owned by ``File.upload_to`` (see
    ``workspace/files/models.py``) and was normalized by migration 0022. Sync
    must read from and register paths under the same root, otherwise it would
    treat every existing file as missing on disk and soft-delete the lot.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="syncuser",
            email="sync@test.com",
            password="pass",
        )

    def _user_root(self):
        return os.path.join(settings.MEDIA_ROOT, "files", "users", self.user.username)

    def _write(self, *parts, contents=b"data"):
        full = os.path.join(self._user_root(), *parts)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(contents)
        return full

    def test_recursive_sync_registers_files_under_canonical_prefix(self):
        self._write("report.pdf", contents=b"%PDF-1.4 test")

        FileSyncService().sync_user_recursive(self.user)

        f = File.objects.get(owner=self.user, name="report.pdf")
        self.assertEqual(
            f.content.name,
            f"files/users/{self.user.username}/report.pdf",
        )

    def test_shallow_sync_at_root_registers_under_canonical_prefix(self):
        self._write("notes.txt", contents=b"hello")

        FileSyncService().sync_folder_shallow(self.user, parent_db=None)

        f = File.objects.get(owner=self.user, name="notes.txt")
        self.assertEqual(
            f.content.name,
            f"files/users/{self.user.username}/notes.txt",
        )

    def test_shallow_sync_inside_subfolder_registers_under_canonical_prefix(self):
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


class SyncUnsafeUsernameTests(IsolatedMediaRootMixin, TestCase):
    """A username that is not a plain path segment must not widen the walk.

    Django's username validator accepts ``..``, and the root of such an
    account, ``files/users/..``, is the parent of every other user's tree: a
    sync that followed it would register their files as the account's own.
    """

    def setUp(self):
        super().setUp()
        alice = User.objects.create_user(username="alice", password="pw")
        FileService.create_file(
            alice, "secret.txt", content=ContentFile(b"alice's secret")
        )
        self.intruder = User.objects.create_user(username="..", password="pw")

    def test_the_recursive_sync_adopts_nothing(self):
        with self.assertRaises(SuspiciousFileOperation):
            FileSyncService().sync_user_recursive(self.intruder)

        self.assertFalse(File.objects.filter(owner=self.intruder).exists())

    def test_the_shallow_sync_adopts_nothing(self):
        with self.assertRaises(SuspiciousFileOperation):
            FileSyncService().sync_folder_shallow(self.intruder)

        self.assertFalse(File.objects.filter(owner=self.intruder).exists())
