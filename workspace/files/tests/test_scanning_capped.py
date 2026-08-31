import io

from django.test import SimpleTestCase

from workspace.files.services.scanning.capped import CappedReader


class CappedReaderTests(SimpleTestCase):
    def test_reads_everything_when_under_the_cap(self):
        reader = CappedReader(io.BytesIO(b"abcdef"), 100)
        self.assertEqual(reader.read(), b"abcdef")
        self.assertFalse(reader.truncated)

    def test_stops_at_the_cap(self):
        reader = CappedReader(io.BytesIO(b"abcdefghij"), 4)
        self.assertEqual(reader.read(), b"abcd")
        self.assertEqual(reader.read(), b"")

    def test_flags_truncation_when_the_source_had_more(self):
        reader = CappedReader(io.BytesIO(b"abcdefghij"), 4)
        reader.read()
        self.assertTrue(reader.truncated)

    def test_exact_fit_is_not_truncated(self):
        reader = CappedReader(io.BytesIO(b"abcd"), 4)
        self.assertEqual(reader.read(), b"abcd")
        self.assertFalse(reader.truncated)

    def test_honours_a_chunk_size(self):
        reader = CappedReader(io.BytesIO(b"abcdefghij"), 6)
        chunks = []
        while chunk := reader.read(4):
            chunks.append(chunk)
        self.assertEqual(chunks, [b"abcd", b"ef"])
        self.assertTrue(reader.truncated)

    def test_empty_source(self):
        reader = CappedReader(io.BytesIO(b""), 10)
        self.assertEqual(reader.read(), b"")
        self.assertFalse(reader.truncated)


class CappedReaderZeroCapTests(SimpleTestCase):
    """A zero cap must still tell an empty source from an unread one."""

    def test_zero_cap_over_a_non_empty_source_is_truncated(self):
        reader = CappedReader(io.BytesIO(b"abc"), 0)
        self.assertEqual(reader.read(), b"")
        self.assertTrue(reader.truncated)

    def test_zero_cap_over_an_empty_source_is_not_truncated(self):
        reader = CappedReader(io.BytesIO(b""), 0)
        self.assertEqual(reader.read(), b"")
        self.assertFalse(reader.truncated)

    def test_the_source_is_probed_at_most_once(self):
        class _CountingStream(io.BytesIO):
            def __init__(self, data):
                super().__init__(data)
                self.reads = 0

            def read(self, size=-1):
                self.reads += 1
                return super().read(size)

        stream = _CountingStream(b"abcdefghij")
        reader = CappedReader(stream, 4)
        reader.read()
        after_first = stream.reads
        for _ in range(3):
            self.assertEqual(reader.read(), b"")
        self.assertEqual(stream.reads, after_first)
