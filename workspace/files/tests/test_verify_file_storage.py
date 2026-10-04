import io
import unicodedata
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management import CommandError, call_command
from django.test import TestCase

from workspace.common.tests.media import IsolatedMediaRootMixin
from workspace.common.tests.s3 import S3StoragesMixin
from workspace.files.models import File
from workspace.files.services import FileService

User = get_user_model()


class VerifyFileStorageCases:
    """The same checks on any backend; the tree mirrors the rows or it does not."""

    def use_storage(self):
        """Point File.content at the storage under test; as configured here."""

    def setUp(self):
        super().setUp()
        self.use_storage()
        self.user = User.objects.create_user(username="alice", password="pw")
        self.storage = File._meta.get_field("content").storage
        self.docs = FileService.create_folder(self.user, "Docs")
        self.file = FileService.create_file(
            self.user, "a.txt", parent=self.docs, content=ContentFile(b"a")
        )

    def verify(self, *args):
        output = io.StringIO()
        call_command("verify_file_storage", *args, stdout=output)
        return output.getvalue()

    def test_a_tree_that_mirrors_its_rows(self):
        trashed = FileService.create_folder(self.user, "Old")
        FileService.create_file(
            self.user, "b.txt", parent=trashed, content=ContentFile(b"b")
        )
        with self.captureOnCommitCallbacks(execute=True):
            FileService.soft_delete(trashed)

        output = self.verify()

        self.assertIn("0 file(s) whose blob is missing", output)
        self.assertIn("0 blob(s) off their file's tree path", output)
        self.assertIn("0 blob(s) no file points at", output)
        self.assertIn("0 folder(s) without a directory", output)

    def test_a_missing_blob_fails(self):
        self.storage.delete(self.file.content.name)

        with self.assertRaises(CommandError):
            self.verify()

    def test_a_blob_no_file_points_at_is_listed(self):
        self.storage.save("files/users/alice/Docs/stray.txt", ContentFile(b"x"))

        output = self.verify()

        self.assertIn("1 blob(s) no file points at", output)
        self.assertIn("files/users/alice/Docs/stray.txt", output)

    def test_a_blob_off_its_tree_path_is_listed(self):
        self.storage.save("files/users/alice/elsewhere.txt", ContentFile(b"a"))
        File.objects.filter(pk=self.file.pk).update(
            content="files/users/alice/elsewhere.txt"
        )

        output = self.verify()

        self.assertIn("1 blob(s) off their file's tree path", output)

    def test_a_folder_without_a_directory_can_be_given_one(self):
        empty = FileService.create_folder(self.user, "Empty")
        self.storage.remove_dir_if_empty("files/users/alice/Empty")

        output = self.verify("--repair")

        self.assertIn("1 folder(s) without a directory", output)
        self.assertIn(f"{empty.pk} files/users/alice/Empty", output)
        self.assertTrue(self.storage.is_dir("files/users/alice/Empty"))
        self.assertIn("0 folder(s) without a directory", self.verify())

    def test_repair_moves_a_blob_back_onto_its_tree_path(self):
        """A blob stored under a name Django rewrote, or that a rename gave
        an extension the node does not have."""
        misplaced = "files/users/alice/Docs/a_renamed.txt"
        self.storage.move(self.file.content.name, misplaced)
        File.objects.filter(pk=self.file.pk).update(content=misplaced)

        with self.captureOnCommitCallbacks(execute=True):
            output = self.verify("--repair")

        self.file.refresh_from_db()
        self.assertEqual(self.file.content.name, "files/users/alice/Docs/a.txt")
        self.assertFalse(self.storage.exists(misplaced))
        with self.file.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"a")
        self.assertIn("0 blob(s) off their file's tree path", output)

    def test_repair_leaves_a_blob_whose_tree_path_is_taken(self):
        misplaced = "files/users/alice/elsewhere.txt"
        self.storage.save(misplaced, ContentFile(b"other"))
        File.objects.filter(pk=self.file.pk).update(content=misplaced)

        output = self.verify("--repair")

        self.assertIn("1 blob(s) left off their tree path", output)
        self.file.refresh_from_db()
        self.assertEqual(self.file.content.name, misplaced)

    def test_repair_repoints_a_file_at_the_blob_on_its_tree_path(self):
        """Only the row was left behind: the blob already sits where the
        node is, and nothing else points at it."""
        File.objects.filter(pk=self.file.pk).update(
            content="files/users/alice/Docs/gone.txt"
        )

        output = self.verify("--repair")

        self.file.refresh_from_db()
        self.assertEqual(self.file.content.name, "files/users/alice/Docs/a.txt")
        self.assertIn("0 file(s) whose blob is missing", output)

    def test_repair_composes_a_name_stored_decomposed(self):
        decomposed = unicodedata.normalize("NFD", "été.txt")
        key = f"files/users/alice/Docs/{decomposed}"
        self.storage.move(self.file.content.name, key)
        File.objects.filter(pk=self.file.pk).update(
            name=decomposed, path=f"Docs/{decomposed}", content=key
        )
        self.assertIn("1 name(s) stored decomposed", self.verify())

        output = self.verify("--repair")

        self.file.refresh_from_db()
        composed = unicodedata.normalize("NFC", "été.txt")
        self.assertEqual(self.file.name, composed)
        self.assertEqual(self.file.content.name, f"files/users/alice/Docs/{composed}")
        self.assertIn("0 name(s) stored decomposed", output)

    def test_repair_settles_a_move_left_unfinished(self):
        FileService.rename(self.docs, "Archive")  # the commit hook never runs
        self.assertIn("1 move(s) not finished yet", self.verify())

        output = self.verify("--repair", "--settle-after", "0")

        self.assertIn("Settled 1 unfinished move(s)", output)
        self.assertIn("0 move(s) not finished yet", output)
        self.assertIn("0 blob(s) no file points at", output)

    def test_repair_leaves_a_recent_move_alone(self):
        FileService.rename(self.docs, "Archive")

        output = self.verify("--repair")

        self.assertIn("Settled 0 unfinished move(s)", output)
        self.assertIn("1 move(s) not finished yet", output)


class VerifyFileStorageOnDiskTests(
    VerifyFileStorageCases, IsolatedMediaRootMixin, TestCase
):
    pass


class VerifyFileStorageOnObjectStorageTests(
    VerifyFileStorageCases, S3StoragesMixin, TestCase
):
    def use_storage(self):
        # File.content resolved its storage when the model was imported.
        patcher = mock.patch.object(
            File._meta.get_field("content"), "storage", storages["files"]
        )
        patcher.start()
        self.addCleanup(patcher.stop)
