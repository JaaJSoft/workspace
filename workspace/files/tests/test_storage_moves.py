"""A move whose blobs cannot follow must not move the rows either.

Rows rewritten to where the bytes never arrived point at nothing: the files
look moved and cannot be opened, and the next sync, not finding them there,
puts them in the trash.
"""

import errno
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.services import FileService

User = get_user_model()


class FailedStorageMoveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mover", password="pw")
        self.docs = FileService.create_folder(self.user, "Docs")
        self.archive = FileService.create_folder(self.user, "Archive")
        self.file = FileService.create_file(
            self.user, "a.txt", parent=self.docs, content=ContentFile(b"a")
        )

    def _renames_fail(self):
        return mock.patch(
            "workspace.common.storage.local.os.rename",
            side_effect=OSError(errno.EXDEV, "Invalid cross-device link"),
        )

    def assert_blob_where_it_was(self):
        self.file.refresh_from_db()
        self.assertEqual(self.file.content.name, "files/users/mover/Docs/a.txt")
        with self.file.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"a")

    def test_a_folder_rename(self):
        with self._renames_fail(), self.assertRaises(OSError):
            FileService.rename(self.docs, "Renamed")

        self.docs.refresh_from_db()
        self.assertEqual(self.docs.name, "Docs")
        self.assert_blob_where_it_was()

    def test_a_folder_move(self):
        with self._renames_fail(), self.assertRaises(OSError):
            FileService.move(self.docs, self.archive)

        self.docs.refresh_from_db()
        self.assertIsNone(self.docs.parent_id)
        self.assert_blob_where_it_was()

    def test_a_file_move(self):
        with self._renames_fail(), self.assertRaises(OSError):
            FileService.move(self.file, self.archive)

        self.file.refresh_from_db()
        self.assertEqual(self.file.parent_id, self.docs.pk)
        self.assert_blob_where_it_was()
