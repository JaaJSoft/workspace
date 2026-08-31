"""What FileService hands a reader, as opposed to what it hands the index.

The assistant's read_file tool goes through here. Search and this helper have
to agree about which files hold words: a file found by a word inside it and
then reported unreadable is the pair coming apart.
"""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.common.documents import extraction
from workspace.common.tests.office_fixtures import ODT as F_ODT
from workspace.common.tests.office_fixtures import make_docx, make_odf, make_xlsx
from workspace.common.tests.pdf_fixtures import make_pdf
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services.text_extraction import has_extractor

User = get_user_model()


class ReadTextContentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")

    def _file(self, name, mime, payload):
        return File.objects.create(
            name=name,
            node_type=File.NodeType.FILE,
            mime_type=mime,
            owner=self.user,
            size=len(payload),
            content=ContentFile(payload, name=name),
        )

    def test_a_plain_text_file_still_reads_as_before(self):
        f = self._file("a.txt", "text/plain", b"quarterly revenue")
        self.assertEqual(FileService.read_text_content(f), "quarterly revenue")

    def test_documents_are_readable_not_just_searchable(self):
        for name, mime, payload in (
            ("report.pdf", "application/pdf", make_pdf(["quarterly budget"])),
            ("minutes.docx", extraction.DOCX, make_docx(["the treasurer resigned"])),
            ("sales.xlsx", extraction.XLSX, make_xlsx(sheets={"S": [["lisbon"]]})),
            ("notes.odt", extraction.ODT, make_odf(F_ODT, ["quarterly budget"])),
        ):
            with self.subTest(mime=mime):
                text = FileService.read_text_content(self._file(name, mime, payload))
                self.assertIsNotNone(text, f"{name} came back unreadable")

    def test_what_search_can_find_a_reader_can_open(self):
        # The invariant behind this file: the two must not disagree about
        # which formats hold words.
        payload = make_docx(["the treasurer resigned"])
        f = self._file("minutes.docx", extraction.DOCX, payload)
        self.assertTrue(has_extractor(f.mime_type))
        self.assertIn("treasurer", FileService.read_text_content(f))

    def test_a_reader_gets_less_than_the_index_does(self):
        # The assistant is handed a budget of its own, well below BODY_CAP.
        payload = make_docx(["the kraken sleeps beneath the waves"] * 500)
        f = self._file("big.docx", extraction.DOCX, payload)
        self.assertLessEqual(len(FileService.read_text_content(f, max_bytes=200)), 200)

    def test_an_unreadable_document_is_not_an_exception(self):
        f = self._file("broken.docx", extraction.DOCX, make_docx(["body"])[:400])
        self.assertIsNone(FileService.read_text_content(f))

    def test_a_scan_has_nothing_to_show(self):
        f = self._file("scan.pdf", "application/pdf", make_pdf([""]))
        self.assertIsNone(FileService.read_text_content(f))

    def test_a_format_with_no_extractor_is_still_refused(self):
        f = self._file("photo.png", "image/png", b"\x89PNG\r\n\x1a\n\xff\xfe")
        self.assertIsNone(FileService.read_text_content(f))
