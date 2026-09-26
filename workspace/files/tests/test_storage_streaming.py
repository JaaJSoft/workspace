"""Moving a blob on storage never holds the whole of it in memory.

A rename runs inside the request (a PATCH, a WebDAV MOVE), where a large
video read whole would weigh on every request sharing the worker.
"""

import tracemalloc

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase

from workspace.files.models import File
from workspace.files.services import FileService, _storage_ops

User = get_user_model()

MB = 1024 * 1024
PAYLOAD = b"0123456789abcdef" * (MB // 16) * 32  # 32 MB


class StreamedBlobMoveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mover", password="pw")
        self.file = File.objects.create(
            owner=self.user,
            name="clip.mp4",
            node_type=File.NodeType.FILE,
            mime_type="video/mp4",
            size=len(PAYLOAD),
            content=ContentFile(PAYLOAD, name="clip.mp4"),
        )

    def _peak_during(self, operation):
        tracemalloc.start()
        self.addCleanup(tracemalloc.stop)
        operation()
        return tracemalloc.get_traced_memory()[1]

    def test_rename_streams_the_blob(self):
        old_path = self.file.content.name
        peak = self._peak_during(lambda: FileService.rename(self.file, "renamed.mp4"))

        self.file.refresh_from_db()
        self.assertLess(peak, 8 * MB)
        self.assertNotEqual(self.file.content.name, old_path)
        self.assertFalse(default_storage.exists(old_path))
        with self.file.content.open("rb") as handle:
            self.assertEqual(handle.read(), PAYLOAD)

    def test_object_storage_relocation_streams_the_blob(self):
        source = self.file.content.name
        destination = source.replace("clip.mp4", "moved.mp4")
        peak = self._peak_during(
            lambda: _storage_ops._relocate_without_paths(source, destination)
        )

        self.assertLess(peak, 8 * MB)
        self.assertFalse(default_storage.exists(source))
        with default_storage.open(destination, "rb") as handle:
            self.assertEqual(handle.read(), PAYLOAD)
