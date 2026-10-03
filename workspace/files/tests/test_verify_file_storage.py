import io
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

        output = self.verify("--fix-dirs")

        self.assertIn("1 folder(s) without a directory", output)
        self.assertIn(f"{empty.pk} files/users/alice/Empty", output)
        self.assertTrue(self.storage.is_dir("files/users/alice/Empty"))
        self.assertIn("0 folder(s) without a directory", self.verify())


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
