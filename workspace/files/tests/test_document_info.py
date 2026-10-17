"""DocumentInfo: the properties a PDF or an office file declares about itself."""

import io
from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfWriter

from workspace.common.tests.office_fixtures import (
    ODT,
    make_docx,
    make_odf,
    make_xlsx,
)
from workspace.common.tests.pdf_fixtures import make_pdf
from workspace.files.models import DocumentInfo, File, FileScan, ProcessingFailure
from workspace.files.services import FileService
from workspace.files.services.document_info import (
    is_document_candidate,
    pending_qs,
    read_document_info,
    refresh_document_info,
)
from workspace.files.services.event_dispatch import run_handlers
from workspace.files.services.processors import get_processor, run_pipeline
from workspace.files.tasks import catch_up_file

from .catch_up import run_catch_up

User = get_user_model()

_PROPERTIES = {
    "title": "Analytical Engine",
    "author": "Ada Lovelace",
    "subject": "Engines",
    "keywords": "math, engine",
    "created": datetime(2024, 5, 1, 12, 0),
    "modified": datetime(2024, 6, 2, 8, 30),
}


def _pdf_with_properties(pages, properties):
    writer = PdfWriter(clone_from=io.BytesIO(make_pdf(pages)))
    writer.add_metadata(properties)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class DocumentInfoTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def _upload(self, name, data):
        return FileService.create_file(
            owner=self.user, name=name, content=ContentFile(data, name=name)
        )

    def _docx(self, name="engine.docx", **properties):
        return self._upload(
            name, make_docx(["body"], properties=properties or _PROPERTIES)
        )

    def _pending(self):
        return set(pending_qs().values_list("pk", flat=True))


class ReadDocumentInfoTests(DocumentInfoTestCase):
    def test_a_word_document(self):
        f = self._docx()

        info = read_document_info(f)

        self.assertEqual(
            (info.title, info.author, info.subject, info.keywords, info.page_count),
            ("Analytical Engine", "Ada Lovelace", "Engines", "math, engine", 1),
        )
        self.assertEqual(info.created, datetime(2024, 5, 1, 12, 0, tzinfo=UTC))
        self.assertEqual(info.modified, datetime(2024, 6, 2, 8, 30, tzinfo=UTC))
        self.assertEqual(info.content_hash, f.content_hash)
        self.assertEqual(self._pending(), set())

    def test_a_pdf(self):
        f = self._upload(
            "kernel.pdf",
            _pdf_with_properties(
                ["a", "b", "c"], {"/Title": "Kernel notes", "/Author": "Linus"}
            ),
        )

        info = read_document_info(f)

        self.assertEqual(
            (info.title, info.author, info.page_count), ("Kernel notes", "Linus", 3)
        )

    def test_a_page_count_the_column_cannot_hold_is_dropped(self):
        # The count is whatever the producer wrote: a negative or 64-bit one
        # must not cost the document the rest of its properties.
        for declared in ("-3", "99999999999"):
            with self.subTest(declared=declared):
                f = self._upload(
                    f"forged{declared}.odt",
                    make_odf(
                        ODT,
                        ["body"],
                        properties={"title": "Forged", "page_count": declared},
                    ),
                )

                info = read_document_info(f)

                self.assertEqual((info.title, info.page_count), ("Forged", None))

    def test_a_spreadsheet_has_no_page_count(self):
        f = self._upload(
            "budget.xlsx", make_xlsx(properties={"creator": "Grace Hopper"})
        )

        info = read_document_info(f)

        self.assertEqual(info.author, "Grace Hopper")
        self.assertIsNone(info.page_count)

    def test_long_properties_are_cut_to_their_column(self):
        f = self._upload(
            "long.pdf", _pdf_with_properties(["body"], {"/Title": "t" * 2000})
        )

        info = read_document_info(f)

        self.assertEqual(len(info.title), 500)

    def test_a_document_the_parser_cannot_read_gets_an_empty_row(self):
        f = self._upload("broken.pdf", b"%PDF-1.4 truncated")
        File.objects.filter(pk=f.pk).update(type="pdf")
        f.refresh_from_db()

        with self.assertLogs("workspace.files.services.document_info", "INFO"):
            info = read_document_info(f)

        self.assertEqual((info.title, info.page_count), ("", None))
        self.assertEqual(self._pending(), set())

    @patch("workspace.files.services.document_info.MAX_DOCUMENT_BYTES", 10)
    def test_a_document_too_large_to_read_gets_an_empty_row_unread(self):
        f = self._docx()

        with (
            patch("workspace.files.services.document_info.extract_document") as extract,
            self.assertLogs("workspace.files.services.document_info", "INFO"),
        ):
            info = read_document_info(f)

        extract.assert_not_called()
        self.assertEqual(info.title, "")
        self.assertEqual(self._pending(), set())

    def test_a_vanished_blob_counts_as_a_failed_attempt(self):
        f = self._docx()
        f.content.storage.delete(f.content.name)

        with self.assertLogs("workspace.files.services.processors", "WARNING"):
            run_catch_up("documents")

        self.assertFalse(DocumentInfo.objects.exists())
        self.assertEqual(
            ProcessingFailure.objects.get(file=f, processor="documents").attempts, 1
        )

    def test_other_files_are_not_read(self):
        f = self._upload("notes.txt", b"some text")

        self.assertFalse(is_document_candidate(f))
        self.assertIsNone(read_document_info(f))
        self.assertEqual(self._pending(), set())

    @override_settings(FILES_MALWARE_SCAN_ENABLED=True)
    def test_a_quarantined_file_is_never_read(self):
        f = self._docx()
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.INFECTED,
            content_hash=f.content_hash,
            scanned_at=timezone.now(),
        )

        with patch(
            "workspace.files.services.document_info.extract_document"
        ) as extract:
            self.assertIsNone(read_document_info(f))
        extract.assert_not_called()
        self.assertEqual(self._pending(), set())

    def test_content_replaced_while_reading_writes_nothing(self):
        f = self._docx()

        def replaced_meanwhile(data, *, max_chars):
            File.objects.filter(pk=f.pk).update(content_hash="f" * 64)
            raise ValueError("whatever was read is moot")

        with (
            patch(
                "workspace.files.services.document_info.extract_document",
                side_effect=replaced_meanwhile,
            ),
            self.assertLogs("workspace.files.services.document_info", "INFO"),
        ):
            self.assertIsNone(read_document_info(f))
        self.assertFalse(DocumentInfo.objects.exists())


class PendingTests(DocumentInfoTestCase):
    def test_a_row_describing_replaced_bytes_is_pending(self):
        f = self._docx()
        read_document_info(f)
        self.assertEqual(self._pending(), set())

        FileService.update_content(
            f,
            ContentFile(
                make_docx(["new"], properties={"title": "Rewritten"}),
                name="engine.docx",
            ),
        )

        self.assertEqual(self._pending(), {f.pk})


class CatchUpTests(DocumentInfoTestCase):
    def test_registered_with_the_catch_up(self):
        self.assertIs(get_processor("documents").process, refresh_document_info)

    def test_backfills_every_existing_document_and_is_idempotent(self):
        docx = self._docx()
        pdf = self._upload("report.pdf", make_pdf(["body"]))
        self._upload("notes.txt", b"some text")

        self.assertEqual(run_catch_up("documents"), 2)
        self.assertEqual(
            set(DocumentInfo.objects.values_list("file_id", flat=True)),
            {docx.pk, pdf.pk},
        )
        self.assertEqual(DocumentInfo.objects.get(file=docx).author, "Ada Lovelace")
        self.assertEqual(run_catch_up("documents"), 0)

    def test_a_document_read_since_it_was_queued_is_not_read_again(self):
        f = self._docx()
        read_document_info(f)

        status = catch_up_file.apply(args=["documents", str(f.pk)]).get()

        self.assertEqual(status, {"status": "skipped"})


class PipelineTests(DocumentInfoTestCase):
    def test_registered_with_the_upload_pipeline(self):
        processor = get_processor("documents")

        self.assertIs(processor.applies_to, is_document_candidate)
        self.assertIs(processor.process, refresh_document_info)

    def test_an_upload_is_read_once_committed(self):
        with patch(
            "workspace.files.tasks.run_file_event_handlers.delay",
            side_effect=run_handlers,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                f = self._docx()

        self.assertEqual(DocumentInfo.objects.get(file=f).title, "Analytical Engine")

    def test_a_document_replaced_by_something_else_loses_its_row(self):
        f = self._docx()
        read_document_info(f)
        File.objects.filter(pk=f.pk).update(type="txt")
        f.refresh_from_db()

        run_pipeline(f.uuid)

        self.assertFalse(DocumentInfo.objects.exists())


class PropertiesPanelTests(DocumentInfoTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def _panel(self, f):
        response = self.client.get(reverse("files_ui:properties", args=[f.uuid]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_document_section_shows_the_properties(self):
        f = self._docx()
        read_document_info(f)

        html = self._panel(f)

        self.assertIn('data-properties-section="document"', html)
        self.assertIn("Analytical Engine", html)
        self.assertIn("Ada Lovelace", html)
        self.assertIn("1 page", html)
        self.assertIn("Written May 1, 2024", html)

    def test_a_row_describing_replaced_bytes_is_not_shown(self):
        f = self._docx()
        read_document_info(f)
        DocumentInfo.objects.filter(file=f).update(content_hash="0" * 64)

        html = self._panel(f)

        self.assertNotIn('data-properties-section="document"', html)
        self.assertNotIn("Ada Lovelace", html)

    def test_a_document_that_declares_nothing_has_no_section(self):
        f = self._upload("blank.pdf", make_pdf([""]))
        DocumentInfo.objects.create(
            file=f, content_hash=f.content_hash, extracted_at=timezone.now()
        )

        self.assertNotIn('data-properties-section="document"', self._panel(f))
