import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase
from pypdf import PdfWriter

from workspace.common.documents import office
from workspace.common.tests.office_fixtures import (
    make_docx,
    make_odf,
    make_pptx,
    make_xlsx,
)
from workspace.common.tests.pdf_fixtures import make_pdf
from workspace.files.models import File
from workspace.files.services.detection import get_all_labels
from workspace.files.services.text_extraction import (
    _MAX_DOCUMENT_BYTES,
    BODY_CAP,
    extract_text,
    has_extractor,
)

User = get_user_model()


class ExtractTextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")

    def _file(self, name, mime, payload):
        return File.objects.create(
            name=name,
            node_type=File.NodeType.FILE,
            mime_type=mime,
            owner=self.user,
            content=ContentFile(payload, name=name),
        )

    def test_markdown_body_is_extracted(self):
        f = self._file("note.md", "text/markdown", b"# Title\n\nThe kraken sleeps.")
        self.assertIn("kraken", extract_text(f))

    def test_plain_text_is_extracted(self):
        f = self._file("a.txt", "text/plain", b"quarterly revenue")
        self.assertEqual(extract_text(f), "quarterly revenue")

    def test_csv_is_extracted(self):
        f = self._file("a.csv", "text/csv", b"name,city\nada,london")
        self.assertIn("london", extract_text(f))

    def test_unknown_text_subtype_is_extracted(self):
        f = self._file("a.rst", "text/x-rst", b"reStructured content")
        self.assertIn("reStructured", extract_text(f))

    def test_mime_parameters_are_ignored(self):
        f = self._file("a.txt", "text/plain; charset=utf-8", b"parameterised")
        self.assertEqual(extract_text(f), "parameterised")

    def test_html_tags_are_stripped(self):
        payload = b"<html><body><p>Visible <b>text</b></p></body></html>"
        f = self._file("a.html", "text/html", payload)
        body = extract_text(f)
        self.assertIn("Visible", body)
        self.assertIn("text", body)
        self.assertNotIn("<p>", body)

    def test_html_script_and_style_bodies_are_dropped(self):
        payload = (
            b"<style>.a{color:red}</style>"
            b"<script>var secretvar = 1;</script>"
            b"<p>keepme</p>"
        )
        f = self._file("a.html", "text/html", payload)
        body = extract_text(f)
        self.assertIn("keepme", body)
        self.assertNotIn("secretvar", body)
        self.assertNotIn("color", body)

    def test_adjacent_html_blocks_stay_separate_words(self):
        # strip_tags puts nothing in a removed tag's place, so without a
        # boundary "<h1>Title</h1><p>Body</p>" becomes the single token
        # "TitleBody" and neither word can ever be found.
        for payload in (
            b"<p>alpha</p><p>beta</p>",
            b"<h1>alpha</h1><p>beta</p>",
            b"<ul><li>alpha</li><li>beta</li></ul>",
            b"<table><tr><td>alpha</td><td>beta</td></tr></table>",
        ):
            with self.subTest(payload=payload):
                words = extract_text(self._file("a.html", "text/html", payload)).split()
                self.assertIn("alpha", words)
                self.assertIn("beta", words)

    def test_html_whitespace_is_collapsed(self):
        # The body is capped, so runs of markup indentation must not eat the
        # budget that real prose needs.
        payload = b"<p>alpha</p>\n\n   \t<p>beta</p>"
        self.assertEqual(
            extract_text(self._file("a.html", "text/html", payload)), "alpha beta"
        )

    def test_html_entities_are_decoded(self):
        f = self._file("a.html", "text/html", b"<p>caf&eacute; &amp; cr&egrave;me</p>")
        self.assertIn("café", extract_text(f))

    def test_text_like_application_types_are_extracted(self):
        # The app files these under the "text" category with a text viewer,
        # so search must see what the viewer shows.
        for name, mime, payload in (
            ("conf.json", "application/json", b'{"region": "lisbon"}'),
            ("feed.xml", "application/xml", b"<city>lisbon</city>"),
            ("app.js", "application/javascript", b"const city = 'lisbon';"),
            ("mod.py", "application/x-python-code", b"CITY = 'lisbon'"),
        ):
            with self.subTest(mime=mime):
                self.assertIn("lisbon", extract_text(self._file(name, mime, payload)))

    def test_a_binary_type_with_no_extractor_yields_nothing(self):
        f = self._file("arch.zip", "application/zip", b"PK lisbon")
        self.assertIsNone(extract_text(f))

    def test_binary_content_yields_nothing(self):
        f = self._file("a.png", "image/png", b"\x89PNG\r\n\x1a\n\xff\xfe")
        self.assertIsNone(extract_text(f))

    def test_undecodable_bytes_declared_as_text_yield_nothing(self):
        f = self._file("a.txt", "text/plain", b"\xff\xfe\xfd\xfc broken")
        self.assertIsNone(extract_text(f))

    def test_folder_yields_nothing(self):
        folder = File.objects.create(
            name="dir", node_type=File.NodeType.FOLDER, owner=self.user
        )
        self.assertIsNone(extract_text(folder))

    def test_missing_blob_yields_nothing(self):
        f = self._file("gone.md", "text/markdown", b"content")
        f.content.storage.delete(f.content.name)
        self.assertIsNone(extract_text(f))

    def test_missing_mime_yields_nothing(self):
        f = File.objects.create(
            name="mystery",
            node_type=File.NodeType.FILE,
            owner=self.user,
            content=ContentFile(b"text", name="mystery"),
        )
        self.assertIsNone(extract_text(f))

    def test_blank_content_yields_nothing(self):
        f = self._file("empty.md", "text/markdown", b"   \n\n  ")
        self.assertIsNone(extract_text(f))

    def test_body_is_capped(self):
        f = self._file("big.md", "text/markdown", b"a " * (BODY_CAP // 2 + 5_000))
        self.assertLessEqual(len(extract_text(f)), BODY_CAP)

    def test_a_cap_landing_mid_codepoint_does_not_lose_the_file(self):
        # Reading N bytes can split a multi-byte character; a strict decode
        # would fail and drop the whole document instead of the last char.
        payload = "é".encode() * (BODY_CAP * 2)
        f = self._file("accents.md", "text/markdown", payload)
        body = extract_text(f)
        self.assertTrue(body.startswith("é"))
        self.assertLessEqual(len(body), BODY_CAP)


class DocumentExtractionTests(TestCase):
    """PDFs and office documents: formats read from a stream, not a prefix."""

    def setUp(self):
        self.user = User.objects.create_user(username="bob", password="pw")

    def _file(self, name, mime, payload):
        return File.objects.create(
            name=name,
            node_type=File.NodeType.FILE,
            mime_type=mime,
            owner=self.user,
            size=len(payload),
            content=ContentFile(payload, name=name),
        )

    def test_pdf_body_is_extracted(self):
        f = self._file("report.pdf", "application/pdf", make_pdf(["quarterly budget"]))
        self.assertIn("quarterly budget", extract_text(f))

    def test_office_bodies_are_extracted(self):
        for name, mime, payload in (
            ("a.docx", office.DOCX, make_docx(["The kraken sleeps."])),
            ("a.xlsx", office.XLSX, make_xlsx(sheets={"S": [["The kraken sleeps."]]})),
            ("a.pptx", office.PPTX, make_pptx([["The kraken sleeps."]])),
            ("a.odt", office.ODT, make_odf(office.ODT, ["The kraken sleeps."])),
            ("a.ods", office.ODS, make_odf(office.ODS, ["The kraken sleeps."])),
            ("a.odp", office.ODP, make_odf(office.ODP, ["The kraken sleeps."])),
        ):
            with self.subTest(mime=mime):
                self.assertIn("kraken", extract_text(self._file(name, mime, payload)))

    def test_a_scan_indexes_its_name_and_nothing_else(self):
        # A scanned page renders its words as pixels: there is no text layer
        # to read, and the file stays findable by its name alone.
        f = self._file("scan.pdf", "application/pdf", make_pdf([""]))
        self.assertIsNone(extract_text(f))

    def test_an_encrypted_pdf_yields_nothing(self):
        writer = PdfWriter(clone_from=io.BytesIO(make_pdf(["secret"])))
        writer.encrypt("hunter2")
        buffer = io.BytesIO()
        writer.write(buffer)

        f = self._file("locked.pdf", "application/pdf", buffer.getvalue())
        self.assertIsNone(extract_text(f))

    def test_corrupt_documents_yield_nothing(self):
        for name, mime, payload in (
            ("broken.pdf", "application/pdf", b"%PDF-1.4 and then nothing usable"),
            ("broken.docx", office.DOCX, b"not an archive at all"),
            ("broken.xlsx", office.XLSX, b"PK\x03\x04 truncated right here"),
            ("broken.odt", office.ODT, b""),
        ):
            with self.subTest(mime=mime):
                self.assertIsNone(extract_text(self._file(name, mime, payload)))

    def test_an_unreadable_document_is_logged_rather_than_raised(self):
        f = self._file("broken.docx", office.DOCX, b"not an archive at all")
        with self.assertLogs("workspace.files.services.text_extraction", "INFO"):
            self.assertIsNone(extract_text(f))

    def test_an_office_body_is_capped(self):
        payload = make_docx(["the kraken sleeps beneath the waves"] * 20_000)
        body = extract_text(self._file("big.docx", office.DOCX, payload))
        self.assertLessEqual(len(body), BODY_CAP)

    def test_a_pdf_body_is_capped(self):
        payload = make_pdf(["a" * 200] * 800)
        body = extract_text(self._file("big.pdf", "application/pdf", payload))
        self.assertLessEqual(len(body), BODY_CAP)

    def test_a_document_past_the_size_ceiling_is_not_read(self):
        f = self._file("huge.docx", office.DOCX, make_docx(["kraken"]))
        File.objects.filter(pk=f.pk).update(size=_MAX_DOCUMENT_BYTES + 1)
        self.assertIsNone(extract_text(File.objects.get(pk=f.pk)))

    def test_a_missing_blob_yields_nothing(self):
        f = self._file("gone.pdf", "application/pdf", make_pdf(["body"]))
        f.content.storage.delete(f.content.name)
        self.assertIsNone(extract_text(f))

    def test_a_stream_that_cannot_seek_is_buffered(self):
        # A storage backend that only streams would otherwise fail deep inside
        # zipfile, on a document local storage reads without trouble.
        f = self._file("a.docx", office.DOCX, make_docx(["kraken"]))
        raw = f.content.storage.open(f.content.name, "rb").read()

        class _ForwardOnly(io.RawIOBase):
            def __init__(self, data):
                self._data = io.BytesIO(data)

            def seekable(self):
                return False

            def read(self, size=-1):
                return self._data.read(size)

        with mock.patch.object(File.content.field.storage, "open") as opener:
            opener.return_value = _ForwardOnly(raw)
            self.assertIn("kraken", extract_text(f))


class ExtractorCoverageTests(SimpleTestCase):
    """A document format the app can recognise but nobody indexes is a decision.

    Detection classifies content into Magika's groups, and "document" is
    precisely the set where a user expects the words inside the file to be
    findable. Checking the registry against that group is what turns "we
    forgot a format" from something noticed by a user into a failing test.
    """

    # Why each of these carries no indexable text. Spelled out, because an
    # empty reason is how a format ends up excluded by accident.
    NOT_INDEXED = {
        # Pre-2007 Office: OLE compound files, not zip containers. They need a
        # different reader and have their own issue.
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        # Zip-and-XML, but neither OOXML nor OpenDocument: their own part
        # layouts, worth their own issue rather than a guess here.
        "application/epub+zip",
        "application/vnd.ms-visio.drawing.main+xml",
        # Proprietary binary containers with no free parser in the stack.
        "application/x-hwp",
        "application/msonenote",
        # A page description language, not a document format: the text is
        # drawing operators, and rendering it is what OCR would be.
        "application/postscript",
        # Magika files these under "document" but reports them as opaque
        # bytes, so there is no format here to key an extractor on.
        "application/octet-stream",
    }

    def _document_mime_types(self):
        return {
            (info.get("mime_type") or "")
            for info in get_all_labels().values()
            if (info.get("group") or "") == "document"
        } - {""}

    def test_every_document_format_is_either_indexed_or_excluded_on_purpose(self):
        declared = self._document_mime_types()
        self.assertTrue(declared, "the detection catalogue reports no documents")
        unclassified = {
            mime
            for mime in declared
            if not has_extractor(mime) and mime not in self.NOT_INDEXED
        }
        self.assertEqual(
            unclassified,
            set(),
            "Detection can recognise these as documents but nothing indexes "
            "them. Register an extractor, or add them to NOT_INDEXED with the "
            "reason they carry no text.",
        )

    def test_the_exclusion_list_does_not_outlive_its_reason(self):
        # An entry that has since gained an extractor has to leave the list, or
        # the list stops being evidence of anything.
        self.assertEqual({m for m in self.NOT_INDEXED if has_extractor(m)}, set())

    def test_every_office_type_the_extractor_supports_is_registered(self):
        # The registration loops over the extractor's own list; this fails if
        # the two ever drift apart.
        for mime in office.SUPPORTED_MIME_TYPES:
            with self.subTest(mime=mime):
                self.assertTrue(has_extractor(mime))
