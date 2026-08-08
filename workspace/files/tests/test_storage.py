"""Storage behaviour backing File.content: stable paths and tuned chunk size."""

import os
import tempfile

from django.core.files.base import ContentFile
from django.core.files.base import File as DjangoFile
from django.test import TestCase, override_settings

from workspace.files.models import File


class FileContentStorageTests(TestCase):
    """The storage attached to File.content must overwrite, never rename."""

    def setUp(self):
        self.storage = File._meta.get_field("content").storage
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def test_same_name_overwrites_instead_of_suffixing(self):
        with override_settings(MEDIA_ROOT=self._tmpdir.name):
            first = self.storage.save("uploads/report.txt", ContentFile(b"first"))
            second = self.storage.save("uploads/report.txt", ContentFile(b"second"))

            self.assertEqual(first, "uploads/report.txt")
            self.assertEqual(second, first)
            with self.storage.open(second) as f:
                self.assertEqual(f.read(), b"second")

    def test_overwrite_truncates_shorter_content(self):
        """A shorter payload must not leave the previous file's tail behind."""
        with override_settings(MEDIA_ROOT=self._tmpdir.name):
            self.storage.save("uploads/data.bin", ContentFile(b"0123456789"))
            name = self.storage.save("uploads/data.bin", ContentFile(b"ab"))

            with self.storage.open(name) as f:
                self.assertEqual(f.read(), b"ab")

    def test_free_name_is_used_verbatim(self):
        with override_settings(MEDIA_ROOT=self._tmpdir.name):
            name = self.storage.save("uploads/fresh.txt", ContentFile(b"x"))

            self.assertEqual(name, "uploads/fresh.txt")


class UploadChunkSizeTests(TestCase):
    """Django's 64 KB default causes hundreds of round-trips on replicated
    storage; the app bumps it at startup. Pins that the wiring actually runs.
    """

    def test_default_chunk_size_is_tuned(self):
        expected = int(os.getenv("FILE_UPLOAD_CHUNK_SIZE", 2 * 1024 * 1024))

        self.assertEqual(DjangoFile.DEFAULT_CHUNK_SIZE, expected)
