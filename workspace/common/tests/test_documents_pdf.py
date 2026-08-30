import io

from django.test import SimpleTestCase
from pypdf import PdfWriter
from pypdf.errors import PdfReadError

from workspace.common.documents.pdf import (
    iter_page_texts,
    open_pdf,
    page_count,
    page_text,
    pdf_text,
    read_metadata,
)
from workspace.common.tests.pdf_fixtures import make_pdf


class PdfTextTests(SimpleTestCase):
    def test_reads_every_page(self):
        text = pdf_text(
            make_pdf(["First page", "Second page"]), max_chars=1000, max_pages=10
        )
        self.assertIn("First page", text)
        self.assertIn("Second page", text)

    def test_accepts_a_stream_as_well_as_bytes(self):
        stream = io.BytesIO(make_pdf(["Streamed"]))
        self.assertIn("Streamed", pdf_text(stream, max_chars=1000, max_pages=10))

    def test_stops_at_the_page_that_fills_the_budget(self):
        # The ceiling has to be honoured by not reading page three, not by
        # reading it and throwing the characters away afterwards.
        text = pdf_text(make_pdf(["aaaa", "bbbb", "cccc"]), max_chars=6, max_pages=10)
        self.assertLessEqual(len(text), 6)
        self.assertNotIn("cccc", text)

    def test_the_page_cap_is_respected(self):
        text = pdf_text(make_pdf(["One", "Two", "Three"]), max_chars=1000, max_pages=2)
        self.assertIn("One", text)
        self.assertNotIn("Three", text)

    def test_a_scan_yields_nothing(self):
        self.assertEqual(pdf_text(make_pdf([""]), max_chars=1000, max_pages=10), "")

    def test_garbage_raises_value_error(self):
        with self.assertRaises(ValueError):
            pdf_text(b"%PDF-1.4 and then nothing usable", max_chars=1000, max_pages=10)


class PageFailureTests(SimpleTestCase):
    def test_an_unreadable_page_costs_only_itself(self):
        class _BrokenPage:
            def extract_text(self):
                raise PdfReadError("unsupported font")

        with self.assertLogs("workspace.common.documents.pdf", level="WARNING"):
            self.assertEqual(page_text(_BrokenPage(), 3), "")


class ReaderTests(SimpleTestCase):
    def _rewritten(self, source, **writer_calls):
        writer = PdfWriter(clone_from=io.BytesIO(source))
        for name, argument in writer_calls.items():
            getattr(writer, name)(argument)
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    def test_a_password_protected_pdf_is_reported(self):
        data = self._rewritten(make_pdf(["Body"]), encrypt="hunter2")
        with self.assertRaises(ValueError) as ctx:
            open_pdf(data)
        self.assertIn("password-protected", str(ctx.exception))

    def test_restricted_printing_is_not_password_protection(self):
        # An empty user password only restricts what a reader may do with the
        # document; the pages themselves open.
        data = self._rewritten(make_pdf(["Readable"]), encrypt="")
        self.assertIn("Readable", pdf_text(data, max_chars=100, max_pages=5))

    def test_reads_title_and_creation_date(self):
        data = self._rewritten(
            make_pdf(["Body"]),
            add_metadata={
                "/Title": "Annual Report",
                "/CreationDate": "D:20240501120000Z",
            },
        )
        self.assertEqual(read_metadata(open_pdf(data)), ("Annual Report", "2024-05-01"))

    def test_missing_metadata_is_empty_rather_than_fatal(self):
        self.assertEqual(read_metadata(open_pdf(make_pdf(["Body"]))), ("", ""))

    def test_a_broken_page_tree_is_a_value_error(self):
        class _BrokenTree:
            @property
            def pages(self):
                raise PdfReadError("broken page tree")

        with self.assertRaises(ValueError):
            page_count(_BrokenTree())

    def test_a_page_that_cannot_be_reached_is_a_value_error(self):
        class _Pages(list):
            def __getitem__(self, index):
                raise PdfReadError("unreachable page")

        class _Reader:
            pages = _Pages([None, None])

        with self.assertRaises(ValueError):
            list(iter_page_texts(_Reader(), max_pages=5))
