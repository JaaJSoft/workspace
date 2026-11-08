"""The data migration that retypes e-books detected as unknown, XML or zip."""

import importlib
import io
import zipfile

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.common.tests.migrations import schema_editor_stub
from workspace.files.models import File, SearchIndexState
from workspace.files.services import FileService
from workspace.files.services.ebooks import EBOOK_FORMATS
from workspace.files.services.search_index import index_file, pending_search_qs
from workspace.files.tests.ebooks import cbz_bytes, fb2_bytes, fbz_bytes, mobi_bytes

migration = importlib.import_module("workspace.files.migrations.0076_retype_ebooks")

User = get_user_model()


class RetypeEbooksTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="retypeebook", password="x")

    def _stored_as(self, name, data, file_type, mime_type="application/octet-stream"):
        """A file as the old detection left it."""
        file_obj = FileService.create_file(
            owner=self.user, name=name, content=ContentFile(data, name=name)
        )
        File.objects.filter(pk=file_obj.pk).update(
            type=file_type, category="unknown", mime_type=mime_type, viewer="text"
        )
        return file_obj

    def _migrate(self):
        migration.retype_ebooks(apps, schema_editor_stub())

    def _typed(self, file_obj):
        return (
            File.objects.filter(pk=file_obj.pk)
            .values_list("type", "category", "mime_type", "viewer")
            .get()
        )

    def test_each_format_stored_under_its_old_label_is_retyped(self):
        rows = [
            (self._stored_as("a.mobi", mobi_bytes(), "unknown"), "mobi"),
            (self._stored_as("b.azw3", mobi_bytes(), "unknown"), "azw3"),
            (self._stored_as("c.fb2", fb2_bytes(), "xml"), "fb2"),
            (self._stored_as("d.fb2.zip", fbz_bytes(), "zip"), "fbz"),
            (self._stored_as("e.cbz", cbz_bytes(), "zip"), "cbz"),
        ]

        self._migrate()

        for file_obj, label in rows:
            with self.subTest(name=file_obj.name):
                self.assertEqual(
                    self._typed(file_obj),
                    (label, "document", EBOOK_FORMATS[label][0], ""),
                )

    def test_a_retyped_book_is_searched_again(self):
        book = self._stored_as("d.fbz", fbz_bytes(), "zip")
        index_file(File.objects.get(pk=book.pk))
        self.assertTrue(SearchIndexState.objects.filter(file_id=book.pk).exists())

        self._migrate()

        self.assertFalse(SearchIndexState.objects.filter(file_id=book.pk).exists())
        self.assertIn(book.pk, pending_search_qs().values_list("pk", flat=True))

    def test_a_file_whose_content_is_not_the_book_its_name_says_keeps_its_type(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "no pages")
        not_a_comic = self._stored_as("f.cbz", buffer.getvalue(), "zip")
        not_a_mobi = self._stored_as("g.mobi", b"plain text, no palm database", "txt")

        self._migrate()

        self.assertEqual(self._typed(not_a_comic)[0], "zip")
        self.assertEqual(self._typed(not_a_mobi)[0], "txt")

    def test_a_vanished_blob_is_skipped(self):
        file_obj = self._stored_as("h.mobi", mobi_bytes(), "unknown")
        file_obj.content.storage.delete(file_obj.content.name)

        self._migrate()

        self.assertEqual(self._typed(file_obj)[0], "unknown")
