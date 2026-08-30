"""Reading text out of OOXML and OpenDocument containers.

Most fixtures are written by the real authoring libraries, so what is asserted
here is what a word processor actually lays out. The hand-built ones are
labelled with what they prove that no library will emit: a rival dialect of the
same format, a container that is malformed on purpose, a part that lies.
"""

import io
from unittest import mock

from django.test import SimpleTestCase

from workspace.common.documents.office import (
    DOCX,
    ODP,
    ODS,
    ODT,
    PPTX,
    XLSX,
    office_text,
)
from workspace.common.tests.office_fixtures import (
    make_docx,
    make_odf,
    make_pptx,
    make_xlsx,
    make_xlsx_shared_strings,
    make_zip,
)

CAP = 10_000

_W_DOCUMENT = (
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
    '2006/main">{}</w:document>'
)
_ODF_CONTENT = (
    '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:'
    'xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text'
    ':1.0"><office:body><office:text>{}</office:text></office:body>'
    "</office:document-content>"
)


def _text(payload, mime_type, *, max_chars=CAP):
    return office_text(io.BytesIO(payload), mime_type, max_chars=max_chars)


class DocxTests(SimpleTestCase):
    def test_paragraph_text_is_extracted(self):
        self.assertIn("kraken", _text(make_docx(["The kraken sleeps."]), DOCX))

    def test_paragraphs_stay_separate_words(self):
        words = _text(make_docx(["alpha", "beta"]), DOCX).split()
        self.assertEqual(words, ["alpha", "beta"])

    def test_a_heading_is_read(self):
        self.assertIn("Quarterly", _text(make_docx([], heading="Quarterly"), DOCX))

    def test_runs_split_mid_word_are_joined_without_a_gap(self):
        # Word splits a sentence into runs on every formatting or spellcheck
        # boundary, mid-word included. Extracting run by run and joining with a
        # separator would turn "kraken" into two tokens that match nothing.
        payload = make_docx([], runs=["The kra", "ken sleeps ", "beneath."])
        self.assertEqual(_text(payload, DOCX), "The kraken sleeps beneath.")

    def test_table_cells_are_read(self):
        payload = make_docx([], table=[["alpha", "beta"]])
        self.assertEqual(_text(payload, DOCX).split(), ["alpha", "beta"])

    def test_headers_footers_and_notes_are_read(self):
        # python-docx writes the header and footer parts; it has no API for
        # footnotes, so that one part is appended by hand.
        payload = make_docx(
            ["body"],
            header="headerword",
            footer="footerword",
            extra={
                "word/footnotes.xml": _W_DOCUMENT.format(
                    "<w:p><w:t>footnoteword</w:t></w:p>"
                )
            },
        )
        body = _text(payload, DOCX)
        for word in ("body", "headerword", "footerword", "footnoteword"):
            self.assertIn(word, body)

    def test_unrelated_parts_are_not_read(self):
        payload = make_docx(
            ["body"],
            extra={
                "word/comments.xml": _W_DOCUMENT.format(
                    "<w:p><w:t>commentword</w:t></w:p>"
                )
            },
        )
        self.assertNotIn("commentword", _text(payload, DOCX))


class PptxTests(SimpleTestCase):
    def test_every_slide_is_read(self):
        body = _text(make_pptx([["first slide"], ["second slide"]]), PPTX)
        self.assertIn("first slide", body)
        self.assertIn("second slide", body)

    def test_every_paragraph_of_a_slide_is_read(self):
        body = _text(make_pptx([["title line", "body line"]]), PPTX)
        self.assertIn("title line", body)
        self.assertIn("body line", body)

    def test_slides_are_read_in_presentation_order(self):
        # Sorted as text, slide10 comes before slide2, so a deck long enough to
        # fill the budget would keep the wrong end of itself.
        payload = make_pptx([[f"slide number {n}"] for n in range(1, 12)])
        body = _text(payload, PPTX, max_chars=32)
        self.assertIn("slide number 1", body)
        self.assertIn("slide number 2", body)
        self.assertNotIn("slide number 10", body)


class XlsxTests(SimpleTestCase):
    """Both ways a cell can hold a string, because both turn up in practice."""

    def test_inline_strings_are_extracted(self):
        # openpyxl's dialect, and what a spreadsheet exported by a Python tool
        # looks like.
        payload = make_xlsx(sheets={"Budget": [["lisbon"]]})
        self.assertIn("lisbon", _text(payload, XLSX))

    def test_shared_strings_are_extracted(self):
        # Excel's dialect. openpyxl never emits it, so this fixture is built by
        # hand or the path is never covered.
        self.assertIn("lisbon", _text(make_xlsx_shared_strings(["lisbon"]), XLSX))

    def test_numbers_are_extracted(self):
        self.assertIn("4712", _text(make_xlsx(sheets={"S": [[4712]]}), XLSX))

    def test_every_sheet_is_read(self):
        payload = make_xlsx(sheets={"One": [["alpha"]], "Two": [["beta"]]})
        body = _text(payload, XLSX)
        self.assertIn("alpha", body)
        self.assertIn("beta", body)

    def test_a_shared_string_index_is_not_indexed_as_a_number(self):
        # A cell of type "s" holds a row number into sharedStrings; indexing it
        # would put the integer 0 in the document instead of the word it names.
        body = _text(make_xlsx_shared_strings(["alpha", "beta"]), XLSX)
        self.assertEqual(body.split(), ["alpha", "beta"])

    def test_rich_text_runs_in_a_shared_string_are_joined(self):
        # Excel splits a styled cell into runs the same way Word splits a
        # paragraph.
        payload = make_zip(
            {
                "xl/sharedStrings.xml": (
                    '<sst xmlns="http://schemas.openxmlformats.org/'
                    'spreadsheetml/2006/main"><si><r><t>quar</t></r>'
                    "<r><t>terly</t></r></si></sst>"
                )
            }
        )
        self.assertEqual(_text(payload, XLSX), "quarterly")


class OpenDocumentTests(SimpleTestCase):
    def test_every_flavour_reads_its_content(self):
        for mime_type in (ODT, ODS, ODP):
            with self.subTest(mime_type=mime_type):
                payload = make_odf(mime_type, ["quarterly budget"])
                self.assertIn("quarterly budget", _text(payload, mime_type))

    def test_headings_are_read(self):
        payload = make_odf(ODT, [], heading="Chapter one")
        self.assertIn("Chapter one", _text(payload, ODT))

    def test_text_around_a_span_survives(self):
        # The span ends first and is released before its paragraph does; a
        # release that dropped the tail would lose the word after it. odfpy
        # writes a span as a child element with no tail, so this shape has to
        # be built by hand.
        payload = make_zip(
            {
                "content.xml": _ODF_CONTENT.format(
                    "<text:p>before <text:span>middle</text:span> after</text:p>"
                )
            }
        )
        self.assertEqual(_text(payload, ODT), "before middle after")


class BudgetTests(SimpleTestCase):
    def test_extraction_stops_at_the_part_that_fills_the_budget(self):
        payload = make_pptx([["aaaa"], ["bbbb"], ["cccc"]])
        body = _text(payload, PPTX, max_chars=6)
        self.assertLessEqual(len(body), 6)
        self.assertNotIn("cccc", body)


class FailureTests(SimpleTestCase):
    def test_a_file_that_is_not_a_zip_raises_value_error(self):
        with self.assertRaises(ValueError):
            _text(b"not an archive at all", DOCX)

    def test_an_unsupported_mime_type_raises_value_error(self):
        with self.assertRaises(ValueError):
            _text(make_docx(["body"]), "application/zip")

    def test_a_container_with_no_content_part_yields_nothing(self):
        self.assertEqual(_text(make_zip({"docProps/app.xml": "<a/>"}), DOCX), "")

    def test_a_part_that_is_not_xml_is_skipped_not_fatal(self):
        payload = make_zip(
            {
                "word/document.xml": "<<<not xml at all",
                "word/footnotes.xml": _W_DOCUMENT.format(
                    "<w:p><w:t>survivor</w:t></w:p>"
                ),
            }
        )
        self.assertIn("survivor", _text(payload, DOCX))


class RivalDialectTests(SimpleTestCase):
    """Shapes that are valid but that no authoring library here will write."""

    def test_the_strict_ooxml_namespace_is_read_too(self):
        # OOXML exists in a transitional and a strict flavour under different
        # namespace URIs. Matching the namespace instead of the local name
        # would index one and silently ignore the other.
        payload = make_zip(
            {
                "word/document.xml": (
                    '<w:document xmlns:w="http://purl.oclc.org/ooxml/'
                    'wordprocessingml/main"><w:body><w:p><w:r><w:t>kraken'
                    "</w:t></w:r></w:p></w:body></w:document>"
                )
            }
        )
        self.assertIn("kraken", _text(payload, DOCX))

    def test_an_undeclared_namespace_prefix_costs_only_its_tag(self):
        # recover=True leaves a tag whose prefix was never declared in the
        # tree, and resolving one through QName raises. A document a producer
        # got slightly wrong must not come back empty.
        payload = make_zip(
            {
                "word/document.xml": _W_DOCUMENT.format(
                    "<w:body><undeclared:mark/><w:p><w:t>kraken</w:t></w:p></w:body>"
                )
            }
        )
        self.assertIn("kraken", _text(payload, DOCX))

    def test_a_paragraph_nested_in_another_is_counted_once(self):
        # A text box puts a drawingml paragraph inside a wordprocessingml one,
        # and both match the prose tag.
        payload = make_zip(
            {
                "word/document.xml": _W_DOCUMENT.format(
                    "<w:body><w:p><w:p>boxed</w:p></w:p></w:body>"
                )
            }
        )
        self.assertEqual(_text(payload, DOCX).split().count("boxed"), 1)


class HardeningTests(SimpleTestCase):
    def test_entities_are_never_expanded(self):
        # Billion laughs: nine nested entities, each ten copies of the one
        # below, is a few hundred bytes that expands to a gigabyte.
        levels = "".join(
            f'<!ENTITY e{level} "{f"&e{level - 1};" * 10}">' for level in range(1, 10)
        )
        doctype = f'<!DOCTYPE w:document [<!ENTITY e0 "boom">{levels}]>'
        payload = make_zip(
            {
                "word/document.xml": doctype
                + _W_DOCUMENT.format("<w:p><w:t>&e9;</w:t></w:p>")
            }
        )
        self.assertNotIn("boom", _text(payload, DOCX))

    def test_an_external_entity_is_never_fetched(self):
        payload = make_zip(
            {
                "word/document.xml": (
                    '<!DOCTYPE w:document [<!ENTITY xxe SYSTEM "file:///etc/'
                    'passwd">]>' + _W_DOCUMENT.format("<w:p><w:t>&xxe;</w:t></w:p>")
                )
            }
        )
        self.assertNotIn("root:", _text(payload, DOCX))

    def test_a_part_is_read_no_further_than_its_ceiling(self):
        # The zip header's uncompressed size is written by whoever built the
        # file, so the bound has to hold on the read itself.
        filler = "<w:p><w:t>pad</w:t></w:p>" * 2000
        payload = make_zip(
            {
                "word/document.xml": _W_DOCUMENT.format(
                    f"<w:body>{filler}<w:p><w:t>trailer</w:t></w:p></w:body>"
                )
            }
        )
        with mock.patch("workspace.common.documents.office.MAX_PART_BYTES", 500):
            body = _text(payload, DOCX)
        self.assertIn("pad", body)
        self.assertNotIn("trailer", body)
