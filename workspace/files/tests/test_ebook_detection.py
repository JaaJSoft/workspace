"""Telling the e-books Magika does not know from the containers they share.

Magika reads a Mobipocket book as unknown data, a FictionBook as XML, and a
comic book or a zipped FictionBook as a plain zip. Each case pins what makes
the file an e-book and, just as much, what does not: a zip of anything is not
a comic because of its name.
"""

import io
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from workspace.files.services import FileService
from workspace.files.services.ebooks import EBOOK_FORMATS, EBOOK_LABELS
from workspace.files.services.filetype import get_icon, get_viewer
from workspace.files.tests.ebooks import (
    cbz_bytes,
    fb2_bytes,
    fbz_bytes,
    mobi_bytes,
)
from workspace.files.ui.viewers import EbookViewer, OfficeViewer

User = get_user_model()


def _zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class EbookDetectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ebooks", password="x")

    def _upload(self, name, data):
        return FileService.create_file(
            owner=self.user, name=name, content=ContentFile(data, name=name)
        )

    def _assert_ebook(self, file_obj, label):
        self.assertEqual(
            (file_obj.type, file_obj.category, file_obj.mime_type),
            (label, "document", EBOOK_FORMATS[label][0]),
        )
        self.assertTrue(file_obj.is_viewable())

    def test_each_format_is_recognised(self):
        for name, data, label in [
            ("book.mobi", mobi_bytes(), "mobi"),
            ("book.prc", mobi_bytes(), "mobi"),
            ("book.azw", mobi_bytes(), "azw"),
            ("book.azw3", mobi_bytes(), "azw3"),
            ("book.fb2", fb2_bytes(), "fb2"),
            ("book.fbz", fbz_bytes(), "fbz"),
            ("book.fb2.zip", fbz_bytes(), "fbz"),
            ("comic.cbz", cbz_bytes(), "cbz"),
        ]:
            with self.subTest(name=name):
                self._assert_ebook(self._upload(name, data), label)

    def test_a_mobipocket_book_is_told_by_its_signature_whatever_its_name(self):
        self._assert_ebook(self._upload("download", mobi_bytes()), "mobi")

    def test_a_fictionbook_is_told_by_its_root_element_whatever_its_name(self):
        self._assert_ebook(self._upload("book.xml", fb2_bytes()), "fb2")

    def test_a_fictionbook_with_a_namespace_prefix_is_left_as_xml(self):
        # The reader matches FictionBook elements by their name as written:
        # <fb:body> is no <body> to it, so the book would not open.
        data = fb2_bytes().replace(b"<FictionBook", b"<fb:FictionBook", 1)
        data = data.replace(b"</FictionBook>", b"</fb:FictionBook>")
        data = data.replace(b'xmlns="', b'xmlns:fb="', 1)

        self.assertNotIn(self._upload("book.fb2", data).type, EBOOK_LABELS)

    def test_other_xml_named_fb2_stays_xml(self):
        file_obj = self._upload(
            "notes.fb2", b'<?xml version="1.0"?>\n<catalog><item>one</item></catalog>\n'
        )

        self.assertNotIn(file_obj.type, EBOOK_LABELS)

    def test_a_zip_of_images_is_only_a_comic_when_named_one(self):
        self.assertEqual(self._upload("holiday.zip", cbz_bytes()).type, "zip")

    def test_a_zip_without_images_named_cbz_stays_a_zip(self):
        data = _zip({"readme.txt": b"no pages here"})

        self.assertEqual(self._upload("comic.cbz", data).type, "zip")

    def test_a_zip_without_a_fictionbook_named_fbz_stays_a_zip(self):
        data = _zip({"cover.png": cbz_bytes()})

        self.assertEqual(self._upload("book.fb2.zip", data).type, "zip")

    def test_replacing_the_content_detects_it_again(self):
        file_obj = self._upload("comic.cbz", _zip({"readme.txt": b"not yet"}))

        FileService.update_content(file_obj, ContentFile(cbz_bytes(), name="comic.cbz"))

        file_obj.refresh_from_db()
        self._assert_ebook(file_obj, "cbz")


class EbookViewerResolutionTests(TestCase):
    def test_every_ebook_label_opens_in_the_reader(self):
        for label in EBOOK_LABELS:
            with self.subTest(label=label):
                self.assertEqual(get_viewer(label), EbookViewer)

    def test_by_mime_type_too(self):
        for label, (mime_type, _, _) in EBOOK_FORMATS.items():
            with self.subTest(label=label):
                self.assertEqual(get_viewer(mime_type), EbookViewer)

    @override_settings(WOPI_DISCOVERY_URL="https://editor/hosting/discovery")
    def test_an_office_editor_advertising_ebooks_does_not_take_them(self):
        advertised = {"docx", *EBOOK_LABELS}
        with patch(
            "workspace.files.services.wopi.discovery.supported_extensions",
            return_value=advertised,
        ):
            self.assertEqual(get_viewer("docx"), OfficeViewer)
            self.assertTrue(EBOOK_LABELS.isdisjoint(OfficeViewer.claimed_labels()))
            for label in EBOOK_LABELS:
                with self.subTest(label=label):
                    self.assertEqual(get_viewer(label), EbookViewer)

    def test_a_book_icon_and_a_comic_icon(self):
        self.assertEqual(get_icon("mobi"), "book-open")
        self.assertEqual(get_icon("fb2"), "book-open")
        self.assertEqual(get_icon("cbz"), "book-image")
