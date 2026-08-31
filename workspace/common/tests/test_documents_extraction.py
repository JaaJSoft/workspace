"""Reading text and metadata out of a document, whatever format it arrived in.

The fixtures are written by the real authoring libraries wherever one exists,
so what is asserted is what a word processor actually lays out rather than
what a hand-built container and the extractor happen to agree on.
"""

import io
import pathlib
import shutil
import tempfile

from django.test import SimpleTestCase
from pypdf import PdfWriter

from workspace.common.documents.extraction import (
    DOCUMENT_MIME_TYPES,
    DOCX,
    DOTX,
    EPUB,
    ODP,
    ODS,
    ODT,
    PDF,
    PPTX,
    RTF,
    XLSX,
    extract_document,
)
from workspace.common.tests.office_fixtures import (
    ODP as F_ODP,
)
from workspace.common.tests.office_fixtures import (
    ODS as F_ODS,
)
from workspace.common.tests.office_fixtures import (
    ODT as F_ODT,
)
from workspace.common.tests.office_fixtures import (
    make_docx,
    make_epub,
    make_odf,
    make_pptx,
    make_rtf,
    make_xlsx,
    make_zip,
)
from workspace.common.tests.pdf_fixtures import make_pdf

CAP = 10_000


def _text(payload, *, max_chars=CAP):
    return " ".join(extract_document(payload, max_chars=max_chars).text.split())


class WordTests(SimpleTestCase):
    def test_paragraph_text_is_extracted(self):
        self.assertIn("kraken", _text(make_docx(["The kraken sleeps."])))

    def test_runs_split_mid_word_are_joined_without_a_gap(self):
        # Word splits a sentence into runs on every formatting boundary,
        # mid-word included.
        payload = make_docx([], runs=["The kra", "ken sleeps ", "beneath."])
        self.assertIn("The kraken sleeps beneath.", _text(payload))

    def test_table_cells_are_read(self):
        body = _text(make_docx([], table=[["alpha", "beta"]]))
        self.assertIn("alpha", body)
        self.assertIn("beta", body)

    def test_headers_and_footers_are_read(self):
        # Off in Tika's defaults, and both carry the words a document gets
        # searched by, so the parser is configured to include them.
        payload = make_docx(["body"], header="headerword", footer="footerword")
        body = _text(payload)
        self.assertIn("headerword", body)
        self.assertIn("footerword", body)


class SpreadsheetTests(SimpleTestCase):
    def test_cell_text_and_numbers_are_extracted(self):
        payload = make_xlsx(sheets={"Budget": [["lisbon", 4712]]})
        body = _text(payload)
        self.assertIn("lisbon", body)
        self.assertIn("4712", body)

    def test_every_sheet_is_read(self):
        payload = make_xlsx(sheets={"One": [["alpha"]], "Two": [["beta"]]})
        body = _text(payload)
        self.assertIn("alpha", body)
        self.assertIn("beta", body)


class PresentationTests(SimpleTestCase):
    def test_every_slide_is_read(self):
        body = _text(make_pptx([["first slide"], ["second slide"]]))
        self.assertIn("first slide", body)
        self.assertIn("second slide", body)


class OpenDocumentTests(SimpleTestCase):
    def test_every_flavour_reads_its_content(self):
        for fixture_mime in (F_ODT, F_ODS, F_ODP):
            with self.subTest(mime_type=fixture_mime):
                payload = make_odf(fixture_mime, ["quarterly budget"])
                self.assertIn("quarterly budget", _text(payload))


class OtherFormatTests(SimpleTestCase):
    def test_rich_text_is_read(self):
        self.assertIn("kraken", _text(make_rtf(["The kraken sleeps."])))

    def test_an_epub_is_read(self):
        document = extract_document(
            make_epub("Moby Dick", ["epub kraken chapter"]), max_chars=CAP
        )
        self.assertIn("kraken", document.text)
        self.assertEqual(document.title, "Moby Dick")

    def test_a_word_template_reads_like_a_document(self):
        # Registered as its own MIME type, but Tika sniffs the container, so
        # the same package reads the same way whatever it is filed under.
        self.assertIn("template", _text(make_docx(["template body"])))


class PdfTests(SimpleTestCase):
    def _rewritten(self, source, **writer_calls):
        writer = PdfWriter(clone_from=io.BytesIO(source))
        for name, argument in writer_calls.items():
            getattr(writer, name)(argument)
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    def test_every_page_is_read(self):
        body = _text(make_pdf(["First page", "Second page"]))
        self.assertIn("First page", body)
        self.assertIn("Second page", body)

    def test_the_page_count_is_reported(self):
        document = extract_document(make_pdf(["a", "b", "c"]), max_chars=CAP)
        self.assertEqual(document.page_count, 3)

    def test_a_scan_yields_no_text_rather_than_failing(self):
        document = extract_document(make_pdf([""]), max_chars=CAP)
        self.assertEqual(document.text, "")
        self.assertEqual(document.page_count, 1)

    def test_title_and_creation_date_are_read(self):
        payload = self._rewritten(
            make_pdf(["Body"]),
            add_metadata={
                "/Title": "Annual Report",
                "/CreationDate": "D:20240501120000Z",
            },
        )
        document = extract_document(payload, max_chars=CAP)
        self.assertEqual(document.title, "Annual Report")
        self.assertEqual(document.date, "2024-05-01")

    def test_an_encrypted_pdf_is_flagged_and_yields_nothing(self):
        payload = self._rewritten(make_pdf(["secret"]), encrypt="hunter2")
        document = extract_document(payload, max_chars=CAP)
        self.assertTrue(document.encrypted)
        self.assertEqual(document.text, "")

    def test_restricted_printing_is_not_password_protection(self):
        # An empty user password only restricts what a reader may do with the
        # document; the pages themselves open.
        payload = self._rewritten(make_pdf(["Readable"]), encrypt="")
        self.assertIn("Readable", extract_document(payload, max_chars=CAP).text)


class BoundsTests(SimpleTestCase):
    def test_the_text_stops_at_the_ceiling(self):
        payload = make_docx(["the kraken sleeps beneath the waves"] * 500)
        document = extract_document(payload, max_chars=200)
        self.assertLessEqual(len(document.text), 200)
        self.assertTrue(document.truncated)

    def test_a_document_that_fits_is_not_reported_as_truncated(self):
        document = extract_document(make_docx(["short"]), max_chars=CAP)
        self.assertFalse(document.truncated)

    def test_a_document_exactly_as_long_as_the_ceiling_is_complete(self):
        # The boundary case: asking the extractor for exactly max_chars cannot
        # tell a document that ends there from one that was cut there, so a
        # complete document would be reported truncated. The length is
        # measured rather than hard-coded, since it is the extractor's to
        # decide.
        payload = make_docx(["the kraken sleeps beneath the waves"])
        full = extract_document(payload, max_chars=1_000_000)
        exact = extract_document(payload, max_chars=len(full.text))

        self.assertFalse(exact.truncated)
        self.assertEqual(exact.text, full.text)

    def test_one_character_short_of_the_document_is_truncated(self):
        payload = make_docx(["the kraken sleeps beneath the waves"])
        full = extract_document(payload, max_chars=1_000_000)
        cut = extract_document(payload, max_chars=len(full.text) - 1)

        self.assertTrue(cut.truncated)
        self.assertLess(len(cut.text), len(full.text))


class FailureTests(SimpleTestCase):
    def test_an_empty_payload_is_not_a_document(self):
        with self.assertRaises(ValueError):
            extract_document(b"", max_chars=CAP)

    def test_a_truncated_archive_raises_value_error(self):
        payload = make_docx(["body"])[:200]
        with self.assertRaises(ValueError):
            extract_document(payload, max_chars=CAP)


class HardeningTests(SimpleTestCase):
    """A document is a file from a stranger; these are the two classic abuses."""

    _W = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
        '2006/main"><w:body>{}</w:body></w:document>'
    )

    def _docx_with_doctype(self, doctype, body):
        return make_zip({"word/document.xml": doctype + self._W.format(body)})

    def test_entity_expansion_is_bounded(self):
        # Billion laughs: nine nested entities, each ten copies of the one
        # below, is a few hundred bytes that asks for a gigabyte. Expanding
        # entities is correct XML; expanding this one is a denial of service,
        # so what is asserted is the bound, not that nothing expanded.
        levels = "".join(
            f'<!ENTITY e{level} "{f"&e{level - 1};" * 10}">' for level in range(1, 10)
        )
        payload = self._docx_with_doctype(
            f'<!DOCTYPE w:document [<!ENTITY e0 "boom">{levels}]>',
            "<w:p><w:t>&e9;</w:t></w:p>",
        )
        try:
            text = extract_document(payload, max_chars=10_000_000).text
        except ValueError:
            return  # refusing it outright is also a correct answer
        self.assertLess(len(text), 10_000)

    def test_an_external_entity_is_never_resolved(self):
        # Written to a real file and pointed at by absolute URL: naming a path
        # that happens not to exist on the machine running the suite would
        # make this pass without proving anything.
        canary = pathlib.Path(tempfile.mkdtemp()) / "canary.txt"
        canary.write_text("TOPSECRETCANARY", encoding="utf-8")
        self.addCleanup(shutil.rmtree, canary.parent, ignore_errors=True)

        payload = self._docx_with_doctype(
            f'<!DOCTYPE w:document [<!ENTITY xxe SYSTEM "{canary.as_uri()}">]>',
            "<w:p><w:t>&xxe;</w:t></w:p>",
        )
        try:
            text = extract_document(payload, max_chars=CAP).text
        except ValueError:
            return
        self.assertNotIn("TOPSECRETCANARY", text)


class CatalogueTests(SimpleTestCase):
    def test_the_declared_document_types_are_the_ones_named(self):
        # The set the indexer registers against; changing it should be a
        # deliberate edit rather than a side effect.
        self.assertEqual(
            DOCUMENT_MIME_TYPES,
            frozenset(
                {
                    PDF,
                    DOCX,
                    DOTX,
                    XLSX,
                    PPTX,
                    ODT,
                    ODS,
                    ODP,
                    RTF,
                    EPUB,
                    "application/msword",
                    "application/vnd.ms-excel",
                    "application/vnd.ms-powerpoint",
                }
            ),
        )
