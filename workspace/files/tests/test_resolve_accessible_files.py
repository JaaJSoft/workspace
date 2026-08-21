"""``FileService.resolve_accessible_files`` - the shared resolve-and-authorize
step behind every "attach workspace files" endpoint (chat, mail, projects)."""

import uuid

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from workspace.files.models import FileShare
from workspace.files.services import FileService

User = get_user_model()


class ResolveAccessibleFilesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="p")
        self.other = User.objects.create_user(username="o", password="p")

    def _make_file(self, owner, name="doc.txt"):
        return FileService.create_file(
            owner,
            name,
            content=SimpleUploadedFile(name, b"x", content_type="text/plain"),
        )

    def test_returns_owned_and_shared_files(self):
        owned = self._make_file(self.user)
        shared = self._make_file(self.other, "shared.txt")
        FileShare.objects.create(
            file=shared,
            shared_by=self.other,
            shared_with=self.user,
            permission=FileShare.Permission.READ_ONLY,
        )
        result = FileService.resolve_accessible_files(
            self.user, [owned.uuid, shared.uuid]
        )
        self.assertEqual({f.uuid for f in result}, {owned.uuid, shared.uuid})

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(FileService.resolve_accessible_files(self.user, []), [])

    def test_duplicates_are_collapsed(self):
        owned = self._make_file(self.user)
        result = FileService.resolve_accessible_files(
            self.user, [owned.uuid, owned.uuid]
        )
        self.assertEqual(len(result), 1)

    def test_unknown_uuid_returns_none(self):
        owned = self._make_file(self.user)
        result = FileService.resolve_accessible_files(
            self.user, [owned.uuid, uuid.uuid4()]
        )
        self.assertIsNone(result)

    def test_foreign_file_returns_none(self):
        foreign = self._make_file(self.other)
        self.assertIsNone(
            FileService.resolve_accessible_files(self.user, [foreign.uuid])
        )

    def test_trashed_file_returns_none(self):
        owned = self._make_file(self.user)
        owned.deleted_at = timezone.now()
        owned.save(update_fields=["deleted_at"])
        self.assertIsNone(FileService.resolve_accessible_files(self.user, [owned.uuid]))

    def test_folder_returns_none(self):
        folder = FileService.create_folder(self.user, "Stuff")
        self.assertIsNone(
            FileService.resolve_accessible_files(self.user, [folder.uuid])
        )
