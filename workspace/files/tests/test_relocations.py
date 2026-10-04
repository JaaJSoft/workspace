"""The journal of unfinished moves, and settling what a crash or a rollback left.

Each case runs on a disk, where a move is a rename and a rollback leaves the
bytes at the destination, and on object storage, where a move copies and a
crash leaves both sides.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from workspace.common.tests.media import IsolatedMediaRootMixin
from workspace.common.tests.s3 import S3StoragesMixin
from workspace.files import tasks as files_tasks
from workspace.files.models import File
from workspace.files.services import FileService, relocations
from workspace.files.sync import FileSyncService

User = get_user_model()


class _RolledBack(Exception):
    """The request failed after the move went through."""


class RelocationCases:
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="alice", password="pw")
        self.docs = FileService.create_folder(self.user, "Docs")
        FileService.create_folder(self.user, "Empty", parent=self.docs)
        self.file = FileService.create_file(
            self.user, "a.txt", parent=self.docs, content=ContentFile(b"bytes")
        )

    def settle(self):
        later = timezone.now() + timedelta(seconds=5)
        return relocations.settle_stale(older_than=timedelta(0), now=later)

    def rename_then_roll_back(self):
        with self.assertRaises(_RolledBack), transaction.atomic():
            FileService.rename(self.docs, "Archive")
            raise _RolledBack

    def blobs(self):
        return sorted(blob.name for blob in default_storage.iter_blobs("files"))

    def assert_tree_mirrors_rows(self):
        self.file.refresh_from_db()
        self.assertEqual(self.blobs(), [self.file.content.name])
        with self.file.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"bytes")

    def test_a_move_is_journaled_until_its_transaction_commits(self):
        FileService.rename(self.docs, "Archive")

        (entry,) = relocations.entries()
        self.assertEqual(entry.source, "files/users/alice/Docs")
        self.assertEqual(entry.destination, "files/users/alice/Archive")

    def test_a_committed_move_ends_its_entry(self):
        with self.captureOnCommitCallbacks(execute=True):
            FileService.rename(self.docs, "Archive")

        self.assertEqual(relocations.entries(), [])
        self.assert_tree_mirrors_rows()

    def test_a_move_that_fails_ends_its_entry(self):
        backend = type(default_storage.backend)
        with (
            mock.patch.object(backend, "relocate", side_effect=OSError("refused")),
            self.assertRaises(OSError),
        ):
            FileService.rename(self.docs, "Archive")

        self.assertEqual(relocations.entries(), [])

    def test_settling_drops_what_a_crash_after_the_commit_left(self):
        """The rows moved, the worker died before dropping the sources."""
        FileService.rename(self.docs, "Archive")  # the commit hook never runs

        settled = self.settle()

        self.assertEqual(settled.entries, 1)
        self.assertEqual(relocations.entries(), [])
        self.assert_tree_mirrors_rows()
        self.assertEqual(self.file.content.name, "files/users/alice/Archive/a.txt")
        self.assertFalse(default_storage.is_dir("files/users/alice/Docs"))

    def test_settling_undoes_a_move_its_transaction_rolled_back(self):
        self.rename_then_roll_back()

        self.settle()

        self.assertEqual(relocations.entries(), [])
        self.assert_tree_mirrors_rows()
        self.assertEqual(self.file.content.name, "files/users/alice/Docs/a.txt")
        self.assertFalse(default_storage.is_dir("files/users/alice/Archive"))

    def test_settling_a_rolled_back_move_keeps_its_empty_folders(self):
        self.rename_then_roll_back()

        self.settle()

        self.assertTrue(default_storage.is_dir("files/users/alice/Docs/Empty"))

    def test_a_young_entry_is_left_to_its_move(self):
        FileService.rename(self.docs, "Archive")

        settled = relocations.settle_stale()

        self.assertEqual(settled.entries, 0)
        self.assertEqual(len(relocations.entries()), 1)

    def test_bytes_no_file_points_at_are_kept(self):
        storage = default_storage
        storage.save("files/users/alice/stray.txt", ContentFile(b"stray"))
        relocations.begin("files/users/alice/stray.txt", "files/users/alice/gone.txt")

        settled = self.settle()

        self.assertEqual(settled.undecided, ["files/users/alice/stray.txt"])
        self.assertTrue(storage.exists("files/users/alice/stray.txt"))

    def test_the_sync_adopts_nothing_a_move_left_unsettled(self):
        """Before settling: neither a copy nor a source comes back as a file,
        and the file whose bytes sit on the other side is not trashed."""
        self.rename_then_roll_back()
        before = set(File.objects.values_list("pk", "deleted_at"))

        FileSyncService().sync_user_recursive(self.user)

        self.assertEqual(set(File.objects.values_list("pk", "deleted_at")), before)

    def test_the_sync_adopts_nothing_a_crash_after_the_commit_left(self):
        FileService.rename(self.docs, "Archive")
        before = set(File.objects.values_list("pk", "deleted_at"))

        FileSyncService().sync_user_recursive(self.user)

        self.assertEqual(set(File.objects.values_list("pk", "deleted_at")), before)

    def test_the_daily_task_settles_entries_older_than_a_day(self):
        FileService.rename(self.docs, "Archive")
        two_days_on = timezone.now() + timedelta(days=2)

        with mock.patch(
            "workspace.files.services.relocations.timezone.now",
            return_value=two_days_on,
        ):
            result = files_tasks.settle_relocations.run()

        self.assertEqual(result["settled"], 1)
        self.assertEqual(relocations.entries(), [])
        self.assert_tree_mirrors_rows()


class RelocationOnDiskTests(RelocationCases, IsolatedMediaRootMixin, TestCase):
    pass


class RelocationOnObjectStorageTests(RelocationCases, S3StoragesMixin, TestCase):
    pass
