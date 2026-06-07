"""Direct unit tests for workspace.common.http_ranges.

These cover the helper module in isolation so the workspace.common
coverage gate stays green; the helpers are also exercised end-to-end
by the chat / mail / files attachment tests, but those don't show up
when CI runs `workspace.common` alone.
"""

import io

from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from workspace.common.http_ranges import (
    parse_byte_range,
    safe_filename,
    serve_with_ranges,
    stream_range,
)


def _make_handle(payload):
    """Return a BytesIO ready for stream_range / serve_with_ranges."""
    return io.BytesIO(payload)


class ParseByteRangeTests(SimpleTestCase):
    """`parse_byte_range` covers every shape of a single byte-range header."""

    SIZE = 1024

    def test_returns_none_for_empty_header(self):
        self.assertIsNone(parse_byte_range("", self.SIZE))
        self.assertIsNone(parse_byte_range(None, self.SIZE))

    def test_returns_none_for_zero_size(self):
        self.assertIsNone(parse_byte_range("bytes=0-99", 0))

    def test_returns_none_for_negative_size(self):
        self.assertIsNone(parse_byte_range("bytes=0-99", -1))

    def test_explicit_range_inclusive(self):
        self.assertEqual(parse_byte_range("bytes=0-99", self.SIZE), (0, 99))
        self.assertEqual(parse_byte_range("bytes=100-199", self.SIZE), (100, 199))

    def test_open_ended_range(self):
        self.assertEqual(parse_byte_range("bytes=512-", self.SIZE), (512, 1023))

    def test_suffix_range(self):
        # Last 50 bytes
        self.assertEqual(parse_byte_range("bytes=-50", self.SIZE), (974, 1023))

    def test_suffix_larger_than_file_clamps_to_zero(self):
        self.assertEqual(parse_byte_range("bytes=-99999", self.SIZE), (0, 1023))

    def test_suffix_zero_is_rejected(self):
        """`bytes=-0` asks for zero bytes - treated as unsatisfiable."""
        self.assertIsNone(parse_byte_range("bytes=-0", self.SIZE))

    def test_end_beyond_eof_is_clamped(self):
        self.assertEqual(parse_byte_range("bytes=900-9999", self.SIZE), (900, 1023))

    def test_start_past_eof_returns_none(self):
        self.assertIsNone(parse_byte_range("bytes=2000-3000", self.SIZE))

    def test_start_equal_to_eof_returns_none(self):
        self.assertIsNone(parse_byte_range(f"bytes={self.SIZE}-", self.SIZE))

    def test_inverted_range_returns_none(self):
        self.assertIsNone(parse_byte_range("bytes=500-100", self.SIZE))

    def test_empty_form_rejected(self):
        """`bytes=-` (no start, no suffix) is malformed."""
        self.assertIsNone(parse_byte_range("bytes=-", self.SIZE))

    def test_malformed_unit_rejected(self):
        self.assertIsNone(parse_byte_range("kilobytes=0-99", self.SIZE))
        self.assertIsNone(parse_byte_range("garbage", self.SIZE))

    def test_strip_handles_outer_whitespace(self):
        """The regex has no internal `\\s*` (ReDoS hardening); outer space is stripped."""
        self.assertEqual(parse_byte_range("  bytes=0-9  ", self.SIZE), (0, 9))

    def test_internal_whitespace_rejected(self):
        """RFC 7233 does not allow internal whitespace; tightened regex enforces this."""
        self.assertIsNone(parse_byte_range("bytes =0-9", self.SIZE))
        self.assertIsNone(parse_byte_range("bytes= 0-9", self.SIZE))
        self.assertIsNone(parse_byte_range("bytes=0 -9", self.SIZE))


class StreamRangeTests(SimpleTestCase):
    """`stream_range` yields the requested slice and closes the handle."""

    def test_yields_exact_slice(self):
        payload = bytes(range(256))
        fh = _make_handle(payload)
        chunks = list(stream_range(fh, 10, 19))
        self.assertEqual(b"".join(chunks), payload[10:20])

    def test_uses_block_size_to_chunk_output(self):
        payload = b"x" * 200
        fh = _make_handle(payload)
        chunks = list(stream_range(fh, 0, 199, block_size=64))
        # 200 bytes / 64 -> 4 chunks (64, 64, 64, 8)
        self.assertEqual(len(chunks), 4)
        self.assertEqual([len(c) for c in chunks], [64, 64, 64, 8])
        self.assertEqual(b"".join(chunks), payload)

    def test_closes_handle_after_exhaustion(self):
        fh = _make_handle(b"abcdef")
        list(stream_range(fh, 0, 5))
        self.assertTrue(fh.closed)

    def test_closes_handle_even_on_generator_abort(self):
        """If the consumer stops iterating early, the finally clause must still close."""
        fh = _make_handle(b"abcdefghij")
        gen = stream_range(fh, 0, 9, block_size=2)
        next(gen)  # pull just one chunk
        gen.close()  # simulate client disconnect
        self.assertTrue(fh.closed)


class SafeFilenameTests(SimpleTestCase):
    """`safe_filename` blocks header injection via CR/LF and quote escaping."""

    def test_strips_cr_lf(self):
        self.assertEqual(
            safe_filename("a\r\nb"),
            "ab",
        )

    def test_escapes_double_quotes(self):
        self.assertEqual(
            safe_filename('na"me.pdf'),
            'na\\"me.pdf',
        )

    def test_escapes_backslash_first(self):
        """Backslash must be doubled BEFORE the quote escape runs."""
        self.assertEqual(
            safe_filename(r'a\b"c'),
            r"a\\b\"c",
        )

    def test_plain_name_unchanged(self):
        self.assertEqual(safe_filename("report.pdf"), "report.pdf")


class ServeWithRangesTests(SimpleTestCase):
    """`serve_with_ranges` glues parsing, streaming, and headers together."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.payload = bytes(i % 256 for i in range(1024))

    def _request(self, range_header=None):
        req = self.factory.get("/whatever")
        if range_header is not None:
            req.META["HTTP_RANGE"] = range_header
        return req

    def _consume(self, response):
        if hasattr(response, "streaming_content"):
            return b"".join(response.streaming_content)
        return response.content

    def test_no_range_returns_fileresponse_with_accept_ranges(self):
        resp = serve_with_ranges(
            self._request(),
            file_handle=_make_handle(self.payload),
            file_size=len(self.payload),
            content_type="video/mp4",
            inline_filename="clip.mp4",
        )
        self.assertIsInstance(resp, FileResponse)
        self.assertEqual(resp["Accept-Ranges"], "bytes")
        self.assertIn("inline", resp["Content-Disposition"])
        self.assertIn("clip.mp4", resp["Content-Disposition"])

    def test_explicit_range_returns_206_with_slice(self):
        resp = serve_with_ranges(
            self._request("bytes=100-199"),
            file_handle=_make_handle(self.payload),
            file_size=len(self.payload),
            content_type="video/mp4",
            inline_filename="clip.mp4",
        )
        self.assertIsInstance(resp, StreamingHttpResponse)
        self.assertEqual(resp.status_code, 206)
        self.assertEqual(resp["Content-Range"], "bytes 100-199/1024")
        self.assertEqual(resp["Content-Length"], "100")
        self.assertEqual(resp["Accept-Ranges"], "bytes")
        self.assertEqual(self._consume(resp), self.payload[100:200])

    def test_unsatisfiable_range_returns_416(self):
        fh = _make_handle(self.payload)
        resp = serve_with_ranges(
            self._request("bytes=9999-"),
            file_handle=fh,
            file_size=len(self.payload),
            content_type="application/octet-stream",
        )
        self.assertIsInstance(resp, HttpResponse)
        self.assertEqual(resp.status_code, 416)
        self.assertEqual(resp["Content-Range"], "bytes */1024")
        self.assertEqual(resp["Accept-Ranges"], "bytes")
        # Handle must be closed even on the 416 short-circuit
        self.assertTrue(fh.closed)

    def test_attachment_filename_sets_attachment_disposition(self):
        resp = serve_with_ranges(
            self._request(),
            file_handle=_make_handle(self.payload),
            file_size=len(self.payload),
            content_type="application/pdf",
            attachment_filename="report.pdf",
        )
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertIn("report.pdf", resp["Content-Disposition"])

    def test_attachment_filename_in_206(self):
        resp = serve_with_ranges(
            self._request("bytes=0-9"),
            file_handle=_make_handle(self.payload),
            file_size=len(self.payload),
            content_type="application/pdf",
            attachment_filename="report.pdf",
        )
        self.assertEqual(resp.status_code, 206)
        self.assertIn("attachment", resp["Content-Disposition"])

    def test_cache_control_propagated_on_200(self):
        resp = serve_with_ranges(
            self._request(),
            file_handle=_make_handle(self.payload),
            file_size=len(self.payload),
            content_type="image/webp",
            inline_filename="icon.webp",
            cache_control="private, max-age=86400, immutable",
        )
        self.assertEqual(resp["Cache-Control"], "private, max-age=86400, immutable")

    def test_cache_control_propagated_on_206(self):
        resp = serve_with_ranges(
            self._request("bytes=0-9"),
            file_handle=_make_handle(self.payload),
            file_size=len(self.payload),
            content_type="video/mp4",
            inline_filename="v.mp4",
            cache_control="private, no-cache",
        )
        self.assertEqual(resp["Cache-Control"], "private, no-cache")

    def test_etag_set_on_200_only(self):
        """ETag must NOT appear on 206 - ConditionalGetMiddleware would
        rewrite it into a 304, starving the media element."""
        resp_200 = serve_with_ranges(
            self._request(),
            file_handle=_make_handle(self.payload),
            file_size=len(self.payload),
            content_type="image/webp",
            inline_filename="x.webp",
            etag='"abc123"',
        )
        self.assertEqual(resp_200.get("ETag"), '"abc123"')

        resp_206 = serve_with_ranges(
            self._request("bytes=0-9"),
            file_handle=_make_handle(self.payload),
            file_size=len(self.payload),
            content_type="video/mp4",
            inline_filename="v.mp4",
            etag='"abc123"',
        )
        self.assertNotIn("ETag", resp_206)

    def test_download_metric_increments_with_bytes_served(self):
        class _Counter:
            def __init__(self):
                self.total = 0

            def inc(self, n):
                self.total += n

        c = _Counter()
        # 200 path: should increment with full file_size
        serve_with_ranges(
            self._request(),
            file_handle=_make_handle(self.payload),
            file_size=len(self.payload),
            content_type="video/mp4",
            inline_filename="v.mp4",
            download_metric=c,
        )
        self.assertEqual(c.total, len(self.payload))

        # 206 path: should increment with sliced size
        serve_with_ranges(
            self._request("bytes=10-19"),
            file_handle=_make_handle(self.payload),
            file_size=len(self.payload),
            content_type="video/mp4",
            inline_filename="v.mp4",
            download_metric=c,
        )
        self.assertEqual(c.total, len(self.payload) + 10)

    def test_download_metric_skipped_when_no_file_size_on_200(self):
        """A zero/None file_size skips the metric increment on the 200 path."""

        class _Counter:
            def __init__(self):
                self.total = 0

            def inc(self, n):
                self.total += n

        c = _Counter()
        serve_with_ranges(
            self._request(),
            file_handle=_make_handle(b""),
            file_size=0,
            content_type="application/octet-stream",
            download_metric=c,
        )
        self.assertEqual(c.total, 0)
