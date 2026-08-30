import io

from django.test import TestCase
from pypdf import PdfWriter

from workspace.ai.services.pdf import extract_pdf
from workspace.common.tests.pdf_fixtures import make_pdf


def _rewritten(source: bytes, **writer_calls) -> bytes:
    """Round-trip a fixture through pypdf, applying *writer_calls* on the way."""
    writer = PdfWriter(clone_from=io.BytesIO(source))
    for name, argument in writer_calls.items():
        getattr(writer, name)(argument)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class ExtractPdfTests(TestCase):
    def test_extracts_every_page(self):
        document = extract_pdf(make_pdf(["First page", "Second page"]))

        self.assertIn("First page", document.text)
        self.assertIn("Second page", document.text)
        self.assertEqual(document.page_count, 2)
        self.assertFalse(document.truncated)

    def test_the_ceiling_stops_extraction_but_still_reports_the_length(self):
        document = extract_pdf(make_pdf(["One", "Two", "Three"]), max_chars=4)

        self.assertLessEqual(len(document.text), 4)
        self.assertTrue(document.truncated)
        self.assertEqual(document.page_count, 3)

    def test_pdf_without_a_text_layer_extracts_nothing(self):
        # A scan renders its words as pixels: the page exists, the text does not.
        document = extract_pdf(make_pdf([""]))

        self.assertEqual(document.text, "")
        self.assertEqual(document.page_count, 1)

    def test_reads_title_and_creation_date(self):
        data = _rewritten(
            make_pdf(["Body"]),
            add_metadata={
                "/Title": "Annual Report",
                "/CreationDate": "D:20240501120000Z",
            },
        )

        document = extract_pdf(data)

        self.assertEqual(document.title, "Annual Report")
        self.assertEqual(document.date, "2024-05-01")

    def test_password_protected_pdf_is_reported(self):
        data = _rewritten(make_pdf(["Body"]), encrypt="hunter2")

        with self.assertRaises(ValueError) as ctx:
            extract_pdf(data)
        self.assertIn("password-protected", str(ctx.exception))

    def test_restricted_printing_still_opens(self):
        # An empty user password restricts what a reader may do, not access.
        data = _rewritten(make_pdf(["Readable"]), encrypt="")

        self.assertIn("Readable", extract_pdf(data).text)

    def test_garbage_bytes_raise_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            extract_pdf(b"%PDF-1.4 and then nothing usable")
        self.assertIn("Could not read PDF", str(ctx.exception))
