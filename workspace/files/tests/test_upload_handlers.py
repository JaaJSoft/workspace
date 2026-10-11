"""Uploads spooled by the hashing handler: the bytes are read once, on arrival."""

import hashlib
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import File as DjangoFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.services import content_hash
from workspace.files.upload_handlers import HashingTemporaryFileUploadHandler

User = get_user_model()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


class HashingUploadHandlerTests(SimpleTestCase):
    def _open(self):
        handler = HashingTemporaryFileUploadHandler()
        handler.new_file("content", "a.bin", "application/octet-stream", None)
        return handler

    def _complete(self, handler, chunks):
        start = 0
        for chunk in chunks:
            handler.receive_data_chunk(chunk, start)
            start += len(chunk)
        upload = handler.file_complete(start)
        self.addCleanup(upload.close)
        return upload

    def test_the_upload_carries_the_digest_of_its_bytes(self):
        upload = self._complete(self._open(), [b"hello ", b"world"])

        self.assertEqual(upload.content_hash, _sha256(b"hello world"))
        self.assertEqual(upload.size, 11)
        self.assertEqual(upload.read(), b"hello world")

    def test_bytes_reach_the_disk_in_blocks_rather_than_parser_chunks(self):
        chunks = [bytes([i]) * 7 for i in range(10)]
        with patch.object(DjangoFile, "DEFAULT_CHUNK_SIZE", 16):
            handler = self._open()
            with patch.object(
                handler, "_write_pending", wraps=handler._write_pending
            ) as write:
                upload = self._complete(handler, chunks)

        # Three blocks of 21 bytes, then the 7-byte tail on completion.
        self.assertEqual(write.call_count, 4)
        self.assertEqual(upload.read(), b"".join(chunks))


# Low enough that a test body goes through the temp-file handler, not memory.
@override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1024)
class SpooledUploadApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.client.force_authenticate(user=self.user)

    def _post(self, data, **extra):
        payload = {
            "name": "big.bin",
            "node_type": "file",
            "content": SimpleUploadedFile("big.bin", data),
            **extra,
        }
        return self.client.post("/api/v1/files", payload, format="multipart")

    def _assert_stored(self, response, data):
        row = File.objects.get(uuid=response.data["uuid"])
        self.assertEqual(row.content_hash, _sha256(data))
        self.assertEqual(row.size, len(data))
        with row.content.open("rb") as fh:
            self.assertEqual(fh.read(), data)

    def test_a_new_file_is_hashed_on_arrival_and_never_read_back(self):
        data = os.urandom(64 * 1024)
        with patch.object(
            content_hash, "hash_stream", wraps=content_hash.hash_stream
        ) as rehash:
            response = self._post(data)

        self.assertEqual(response.status_code, 201, response.content)
        rehash.assert_not_called()
        self._assert_stored(response, data)

    def test_a_replacing_upload_is_hashed_on_arrival_too(self):
        self.assertEqual(self._post(os.urandom(64 * 1024)).status_code, 201)
        data = os.urandom(80 * 1024)
        with patch.object(
            content_hash, "hash_stream", wraps=content_hash.hash_stream
        ) as rehash:
            response = self._post(data, on_conflict="replace")

        self.assertEqual(response.status_code, 200, response.content)
        rehash.assert_not_called()
        self._assert_stored(response, data)
