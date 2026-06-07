from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File

User = get_user_model()


def _body(response):
    """Collect the response body from either streaming_content or content."""
    if hasattr(response, "streaming_content"):
        return b"".join(response.streaming_content)
    return response.content


class ContentRangeTests(APITestCase):
    """HTTP Range support on GET /api/v1/files/<uuid>/content."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="range-user", email="range@example.com", password="pass123"
        )
        self.client.force_authenticate(user=self.user)
        # 1 KiB of recognizable bytes: positions 0..1023 carry value (i % 256)
        self.payload = bytes(i % 256 for i in range(1024))
        self.file = File(
            owner=self.user,
            name="clip.mp4",
            node_type=File.NodeType.FILE,
            mime_type="video/mp4",
        )
        self.file.content = ContentFile(self.payload, name="clip.mp4")
        self.file.size = len(self.payload)
        self.file.save()
        self.url = f"/api/v1/files/{self.file.uuid}/content"

    def test_no_range_returns_200_with_accept_ranges(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Accept-Ranges"], "bytes")
        self.assertEqual(_body(resp), self.payload)

    def test_explicit_range_returns_206_with_correct_slice(self):
        resp = self.client.get(self.url, HTTP_RANGE="bytes=100-199")
        self.assertEqual(resp.status_code, status.HTTP_206_PARTIAL_CONTENT)
        self.assertEqual(resp["Content-Range"], f"bytes 100-199/{len(self.payload)}")
        self.assertEqual(resp["Content-Length"], "100")
        self.assertEqual(resp["Accept-Ranges"], "bytes")
        body = _body(resp)
        self.assertEqual(len(body), 100)
        self.assertEqual(body, self.payload[100:200])

    def test_open_ended_range_streams_to_eof(self):
        resp = self.client.get(self.url, HTTP_RANGE="bytes=1000-")
        self.assertEqual(resp.status_code, status.HTTP_206_PARTIAL_CONTENT)
        self.assertEqual(resp["Content-Range"], f"bytes 1000-1023/{len(self.payload)}")
        self.assertEqual(_body(resp), self.payload[1000:])

    def test_suffix_range_returns_last_n_bytes(self):
        resp = self.client.get(self.url, HTTP_RANGE="bytes=-50")
        self.assertEqual(resp.status_code, status.HTTP_206_PARTIAL_CONTENT)
        self.assertEqual(resp["Content-Range"], f"bytes 974-1023/{len(self.payload)}")
        self.assertEqual(_body(resp), self.payload[-50:])

    def test_suffix_range_larger_than_file_clamps_to_full(self):
        resp = self.client.get(self.url, HTTP_RANGE="bytes=-9999")
        self.assertEqual(resp.status_code, status.HTTP_206_PARTIAL_CONTENT)
        self.assertEqual(resp["Content-Range"], f"bytes 0-1023/{len(self.payload)}")
        self.assertEqual(_body(resp), self.payload)

    def test_end_beyond_file_is_clamped(self):
        resp = self.client.get(self.url, HTTP_RANGE="bytes=900-9999")
        self.assertEqual(resp.status_code, status.HTTP_206_PARTIAL_CONTENT)
        self.assertEqual(resp["Content-Range"], f"bytes 900-1023/{len(self.payload)}")
        self.assertEqual(_body(resp), self.payload[900:])

    def test_start_past_eof_returns_416(self):
        resp = self.client.get(self.url, HTTP_RANGE="bytes=2000-3000")
        self.assertEqual(resp.status_code, 416)
        self.assertEqual(resp["Content-Range"], f"bytes */{len(self.payload)}")

    def test_malformed_range_returns_416(self):
        resp = self.client.get(self.url, HTTP_RANGE="kilobytes=0-10")
        self.assertEqual(resp.status_code, 416)

    def test_range_bypasses_304_short_circuit(self):
        """A Range request must serve the slice even when the ETag matches."""
        # First, fetch full file to learn the ETag
        warmup = self.client.get(self.url)
        etag = warmup["ETag"]
        # Now issue a Range request with matching If-None-Match. Must serve 206, not 304.
        resp = self.client.get(
            self.url,
            HTTP_RANGE="bytes=0-9",
            HTTP_IF_NONE_MATCH=etag,
        )
        self.assertEqual(resp.status_code, status.HTTP_206_PARTIAL_CONTENT)
        self.assertEqual(_body(resp), self.payload[:10])

    def test_text_response_advertises_accept_ranges(self):
        """Even on the text fast path, clients must learn Range is supported."""
        text_file = File(
            owner=self.user,
            name="note.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        text_file.content = ContentFile(b"hello world", name="note.txt")
        text_file.size = 11
        text_file.save()

        resp = self.client.get(f"/api/v1/files/{text_file.uuid}/content")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Accept-Ranges"], "bytes")
