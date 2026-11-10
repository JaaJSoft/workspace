import io
import uuid
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.tests.operations import outcome_of, run_operation

User = get_user_model()


def _make_zip(entries):
    """A zip whose bytes do not depend on when the test runs.

    ``writestr`` with a bare name stamps each entry with the current time,
    and the type detection (an ML model) files a few of those byte layouts
    as ``unknown`` rather than ``zip``: the extract endpoint then refuses
    the archive before reaching the check under test. A fixed timestamp
    keeps every run on bytes that detect as a zip.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    buf.seek(0)
    return buf.read()


class ExtractApiTests(APITestCase):
    """Extracting an archive through the batch operations API."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)
        self.dest = FileService.create_folder(self.user, "dest")

    def _make_archive(self, payload=None, mime="application/zip", name="archive.zip"):
        payload = payload if payload is not None else _make_zip([("hello.txt", b"hi")])
        return FileService.create_file(
            self.user,
            name,
            parent=None,
            content=ContentFile(payload, name=name),
            mime_type=mime,
        )

    def _extract(self, archive, target):
        return run_operation(self.client, "extract", [archive], target)

    def test_extract_happy_path(self):
        response = self._extract(self._make_archive(), self.dest)

        self.assertEqual(outcome_of(response), ({"done": 1}, []))
        self.assertEqual(response.json()["target"], str(self.dest.uuid))
        self.assertTrue(
            File.objects.filter(parent=self.dest, name="hello.txt").exists()
        )

    def test_an_unknown_archive_is_not_found(self):
        response = self._extract(uuid.uuid4(), self.dest)

        self.assertEqual(outcome_of(response), ({"failed": 1}, [("", "not_found")]))

    def test_a_malformed_destination_is_refused(self):
        response = self._extract(self._make_archive(), "not-a-uuid")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_extract_to_root_when_destination_is_null(self):
        response = self._extract(self._make_archive(), None)

        self.assertEqual(outcome_of(response), ({"done": 1}, []))
        self.assertIsNone(response.json()["target"])
        created = File.objects.get(
            owner=self.user, parent=None, name="hello.txt", node_type="file"
        )
        self.assertEqual(created.content.read(), b"hi")

    def test_someone_elses_folder_answers_like_a_missing_one(self):
        other = User.objects.create_user(
            username="bob", email="bob@example.com", password="pw"
        )
        their_folder = FileService.create_folder(other, "theirs")
        archive = self._make_archive()

        hidden = self._extract(archive, their_folder)
        absent = self._extract(archive, uuid.uuid4())

        self.assertEqual(hidden.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )
        self.assertFalse(File.objects.filter(parent=their_folder).exists())

    def test_only_an_archive_is_extracted(self):
        archive = self._make_archive(
            payload=b"plain text", mime="text/plain", name="note.txt"
        )

        response = self._extract(archive, self.dest)

        self.assertEqual(
            outcome_of(response), ({"failed": 1}, [("note.txt", "not_allowed")])
        )

    def test_a_corrupted_archive_cannot_be_read(self):
        archive = self._make_archive(payload=b"PK\x03\x04 garbage")
        archive.type = "zip"
        archive.save(update_fields=["type"])

        with self.assertLogs("workspace.files.services.extract", level="INFO"):
            response = self._extract(archive, self.dest)

        self.assertEqual(
            outcome_of(response), ({"failed": 1}, [("archive.zip", "archive_invalid")])
        )
        self.assertEqual(
            response.json()["errors"][0]["message"], "The archive cannot be read."
        )

    def test_extract_happy_path_with_x_zip_compressed_mime(self):
        response = self._extract(
            self._make_archive(mime="application/x-zip-compressed"), self.dest
        )

        self.assertEqual(outcome_of(response), ({"done": 1}, []))
        self.assertTrue(
            File.objects.filter(parent=self.dest, name="hello.txt").exists()
        )

    @override_settings(FILES_EXTRACT_MAX_BYTES=5)
    def test_an_archive_over_the_size_limit_is_refused(self):
        archive = self._make_archive(payload=_make_zip([("big.txt", b"X" * 100)]))

        response = self._extract(archive, self.dest)

        self.assertEqual(
            outcome_of(response),
            ({"failed": 1}, [("archive.zip", "archive_too_large")]),
        )

    @override_settings(FILES_EXTRACT_MAX_ENTRIES=1)
    def test_an_archive_with_too_many_entries_is_refused(self):
        archive = self._make_archive(
            payload=_make_zip([("a.txt", b"a"), ("b.txt", b"b")])
        )

        response = self._extract(archive, self.dest)

        self.assertEqual(
            outcome_of(response),
            ({"failed": 1}, [("archive.zip", "archive_too_many_entries")]),
        )

    def test_an_archive_whose_blob_vanished_cannot_be_read(self):
        archive = self._make_archive()
        archive.content.storage.delete(archive.content.name)

        with self.assertLogs("workspace.files.services.extract", level="INFO"):
            response = self._extract(archive, self.dest)

        self.assertEqual(
            outcome_of(response), ({"failed": 1}, [("archive.zip", "archive_invalid")])
        )
