"""Regression tests for content-label refinement by filename extension.

Magika classifies a sparse Markdown file (e.g. ``# Title\n``) as ``txt``. The
stored ``File.type`` must still come out as ``markdown`` so the note shows up in
the notes browser and the ``[[`` search (both filter ``type=markdown``). See
``refine_with_name`` in ``workspace/files/services/detection.py``.
"""
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.services import FileService


class RefineWithNameTest(TestCase):
    def test_generic_txt_refined_to_markdown_by_md_extension(self):
        from workspace.files.services.detection import refine_with_name
        self.assertEqual(refine_with_name("txt", "notes.md"), "markdown")

    def test_specific_content_label_is_kept(self):
        from workspace.files.services.detection import refine_with_name
        # Confident binary content wins over a misleading .md name.
        self.assertEqual(refine_with_name("png", "notes.md"), "png")

    def test_no_refinement_toward_non_text_extension(self):
        from workspace.files.services.detection import refine_with_name
        # A text blob misnamed .png stays txt, not png.
        self.assertEqual(refine_with_name("txt", "photo.png"), "txt")

    def test_unknown_extension_keeps_label(self):
        from workspace.files.services.detection import refine_with_name
        self.assertEqual(refine_with_name("txt", "data.xyz123"), "txt")

    def test_no_extension_keeps_label(self):
        from workspace.files.services.detection import refine_with_name
        self.assertEqual(refine_with_name("txt", "READSME"), "txt")


class StoredTypeHonoursExtensionTest(TestCase):
    """End-to-end: a sparse .md created/updated through FileService is stored
    with ``type='markdown'``, not the ``txt`` Magika reads from the content."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("md_detect", password="x")

    def test_create_file_sparse_markdown_is_tagged_markdown(self):
        f = FileService.create_file(
            self.user, "sparse.md",
            content=ContentFile(b"# Title\n", name="sparse.md"),
            mime_type="text/markdown",
        )
        self.assertEqual(f.type, "markdown")

    def test_update_content_sparse_markdown_is_tagged_markdown(self):
        f = FileService.create_file(
            self.user, "note.md",
            content=ContentFile(
                b"# Rich\n\nA real paragraph of content here.\n", name="note.md"
            ),
        )
        FileService.update_content(
            f, ContentFile(b"# Tiny\n", name="note.md"), name="note.md",
        )
        f.refresh_from_db()
        self.assertEqual(f.type, "markdown")
