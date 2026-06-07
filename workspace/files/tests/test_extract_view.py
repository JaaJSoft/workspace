import io
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.services import FileService

User = get_user_model()


def _make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    buf.seek(0)
    return buf.read()


class ExtractViewTests(APITestCase):
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

    def _url(self, archive):
        return f"/api/v1/files/{archive.uuid}/extract"

    def test_extract_happy_path(self):
        archive = self._make_archive()
        resp = self.client.post(
            self._url(archive),
            {"destination_uuid": str(self.dest.uuid)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertEqual(resp.json()["files_created"], 1)
        self.assertEqual(resp.json()["destination_uuid"], str(self.dest.uuid))
        self.assertTrue(
            File.objects.filter(parent=self.dest, name="hello.txt").exists()
        )

    def test_extract_404_when_source_missing(self):
        resp = self.client.post(
            "/api/v1/files/00000000-0000-0000-0000-000000000000/extract",
            {"destination_uuid": str(self.dest.uuid)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_extract_400_for_garbage_destination_uuid(self):
        archive = self._make_archive()
        resp = self.client.post(
            self._url(archive),
            {"destination_uuid": "not-a-uuid"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_extract_400_when_destination_key_absent(self):
        archive = self._make_archive()
        resp = self.client.post(self._url(archive), {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_extract_to_root_when_destination_is_null(self):
        archive = self._make_archive()
        resp = self.client.post(
            self._url(archive),
            {"destination_uuid": None},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        body = resp.json()
        self.assertEqual(body["files_created"], 1)
        self.assertIsNone(body["destination_uuid"])
        created = File.objects.get(
            owner=self.user,
            parent=None,
            name="hello.txt",
            node_type="file",
        )
        self.assertEqual(created.content.read(), b"hi")

    def test_extract_404_when_destination_not_owned(self):
        other = User.objects.create_user(
            username="bob", email="bob@example.com", password="pw"
        )
        other_folder = FileService.create_folder(other, "theirs")
        archive = self._make_archive()
        resp = self.client.post(
            self._url(archive),
            {"destination_uuid": str(other_folder.uuid)},
            format="json",
        )
        # Must be 404 (not 403) AND the detail must match the "not found" branch
        # so an attacker can't tell "folder exists but I have no permission"
        # apart from "folder does not exist".
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(resp.json()["detail"], "Destination folder not found.")

    def test_extract_400_for_non_zip_mime(self):
        archive = self._make_archive(
            payload=b"plain text", mime="text/plain", name="note.txt"
        )
        resp = self.client.post(
            self._url(archive),
            {"destination_uuid": str(self.dest.uuid)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_extract_400_for_corrupted_archive(self):
        archive = self._make_archive(payload=b"PK\x03\x04 garbage")
        resp = self.client.post(
            self._url(archive),
            {"destination_uuid": str(self.dest.uuid)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_extract_happy_path_with_x_zip_compressed_mime(self):
        archive = self._make_archive(mime="application/x-zip-compressed")
        resp = self.client.post(
            self._url(archive),
            {"destination_uuid": str(self.dest.uuid)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertEqual(resp.json()["files_created"], 1)
        self.assertTrue(
            File.objects.filter(parent=self.dest, name="hello.txt").exists()
        )

    @override_settings(FILES_EXTRACT_MAX_BYTES=5)
    def test_extract_413_when_size_exceeded(self):
        payload = _make_zip([("big.txt", b"X" * 100)])
        archive = self._make_archive(payload=payload)
        resp = self.client.post(
            self._url(archive),
            {"destination_uuid": str(self.dest.uuid)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    @override_settings(FILES_EXTRACT_MAX_ENTRIES=1)
    def test_extract_413_when_entry_count_exceeded(self):
        payload = _make_zip(
            [
                ("a.txt", b"a"),
                ("b.txt", b"b"),
            ]
        )
        archive = self._make_archive(payload=payload)
        resp = self.client.post(
            self._url(archive),
            {"destination_uuid": str(self.dest.uuid)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    def test_extract_404_when_archive_blob_missing(self):
        """If the archive's underlying storage blob has vanished (deleted out
        of band), the endpoint must report 404, not 400. Matches the chat /
        mail attachment convention for missing blobs."""
        archive = self._make_archive()
        # Simulate a vanished blob: delete the stored file but keep the DB row.
        archive.content.storage.delete(archive.content.name)
        resp = self.client.post(
            self._url(archive),
            {"destination_uuid": str(self.dest.uuid)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
