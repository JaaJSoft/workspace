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
