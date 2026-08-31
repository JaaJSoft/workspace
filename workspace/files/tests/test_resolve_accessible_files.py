"""``FileService.resolve_accessible_files`` - the shared resolve-and-authorize
step behind every "attach workspace files" endpoint (chat, mail, projects)."""

import uuid

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files.models import FileScan, FileShare
from workspace.files.services import FileService

User = get_user_model()
BLOCKING = {
    "FILES_MALWARE_SCAN_ENABLED": True,
    "FILES_MALWARE_ON_DETECTION": "block",
}
FLAGGING = {
    "FILES_MALWARE_SCAN_ENABLED": True,
    "FILES_MALWARE_ON_DETECTION": "flag",
}


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

    def _infected(self, name="bad.txt"):
        f = self._make_file(self.user, name)
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at="2026-08-30T12:00:00Z",
        )
        return f

    @override_settings(**BLOCKING)
    def test_quarantined_file_returns_none(self):
        """Attaching copies the blob into a table with no scan row, so a
        blocked file must never make it past this chokepoint."""
        blocked = self._infected()
        clean = self._make_file(self.user, "ok.txt")
        self.assertIsNone(
            FileService.resolve_accessible_files(self.user, [blocked.uuid])
        )
        # All-or-nothing: one blocked uuid rejects the whole batch.
        self.assertIsNone(
            FileService.resolve_accessible_files(self.user, [clean.uuid, blocked.uuid])
        )

    @override_settings(**FLAGGING)
    def test_flagged_file_is_still_attachable(self):
        flagged = self._infected("flagged.txt")
        result = FileService.resolve_accessible_files(self.user, [flagged.uuid])
        self.assertEqual({f.uuid for f in result}, {flagged.uuid})
