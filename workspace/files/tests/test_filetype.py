from django.test import TestCase

from workspace.files.services.filetype import (
    FileTypeInfo,
    get_color,
    get_group,
    get_icon,
    get_info,
    get_mime_type,
    get_viewer,
    is_viewable,
)
from workspace.files.ui.viewers import (
    ImageViewer,
    MarkdownViewer,
    MediaViewer,
    PDFViewer,
    TextViewer,
)


class GroupResolutionTest(TestCase):
    def test_python_is_code(self):
        self.assertEqual(get_group("python"), "code")

    def test_javascript_is_code(self):
        self.assertEqual(get_group("javascript"), "code")

    def test_jpeg_is_image(self):
        self.assertEqual(get_group("jpeg"), "image")

    def test_png_is_image(self):
        self.assertEqual(get_group("png"), "image")

    def test_pdf_is_document(self):
        self.assertEqual(get_group("pdf"), "document")

    def test_mp4_is_video(self):
        self.assertEqual(get_group("mp4"), "video")

    def test_mp3_is_audio(self):
        self.assertEqual(get_group("mp3"), "audio")

    def test_zip_is_archive(self):
        self.assertEqual(get_group("zip"), "archive")

    def test_markdown_is_text(self):
        self.assertEqual(get_group("markdown"), "text")

    def test_csv_is_code(self):
        self.assertEqual(get_group("csv"), "code")

    def test_unknown_label_returns_unknown(self):
        self.assertEqual(get_group(""), "unknown")

    def test_nonexistent_label_returns_unknown(self):
        self.assertEqual(get_group("totally_fake_label"), "unknown")

    def test_ungrouped_text_label_gets_code(self):
        """Labels with is_text=True but no group should fall into 'code'."""
        from workspace.files.services.detection import get_all_labels

        kb = get_all_labels()
        text_no_group = [
            label
            for label, info in kb.items()
            if info.get("is_text") and not info.get("group")
        ]
        for label in text_no_group:
            self.assertEqual(
                get_group(label),
                "code",
                msg=f"{label} has is_text=True and no group, expected 'code'",
            )


class IconMappingTest(TestCase):
    def test_code_group_icon(self):
        self.assertEqual(get_icon("python"), "file-code")

    def test_text_group_icon(self):
        self.assertEqual(get_icon("markdown"), "file-text")

    def test_image_group_icon(self):
        self.assertEqual(get_icon("jpeg"), "image")

    def test_video_group_icon(self):
        self.assertEqual(get_icon("mp4"), "video")

    def test_audio_group_icon(self):
        self.assertEqual(get_icon("mp3"), "music")

    def test_archive_group_icon(self):
        self.assertEqual(get_icon("zip"), "file-archive")

    def test_document_group_icon(self):
        self.assertEqual(get_icon("docx"), "file-text")

    def test_default_icon_for_unknown(self):
        self.assertEqual(get_icon(""), "file")

    def test_json_override(self):
        self.assertEqual(get_icon("json"), "file-json")

    def test_jsonl_override(self):
        self.assertEqual(get_icon("jsonl"), "file-json")

    def test_csv_override(self):
        self.assertEqual(get_icon("csv"), "file-spreadsheet")

    def test_tsv_override(self):
        self.assertEqual(get_icon("tsv"), "file-spreadsheet")

    def test_xlsx_override(self):
        self.assertEqual(get_icon("xlsx"), "file-spreadsheet")

    def test_xls_override(self):
        self.assertEqual(get_icon("xls"), "file-spreadsheet")

    def test_ods_override(self):
        self.assertEqual(get_icon("ods"), "file-spreadsheet")

    def test_pptx_override(self):
        self.assertEqual(get_icon("pptx"), "file-presentation")

    def test_ppt_override(self):
        self.assertEqual(get_icon("ppt"), "file-presentation")

    def test_odp_override(self):
        self.assertEqual(get_icon("odp"), "file-presentation")

    def test_dockerfile_override(self):
        self.assertEqual(get_icon("dockerfile"), "container")

    def test_svg_override(self):
        self.assertEqual(get_icon("svg"), "image")

    def test_epub_override(self):
        self.assertEqual(get_icon("epub"), "book-open")


class ColorMappingTest(TestCase):
    def test_code_group_color(self):
        self.assertEqual(get_color("python"), "text-info")

    def test_image_group_color(self):
        self.assertEqual(get_color("jpeg"), "text-success")

    def test_video_group_color(self):
        self.assertEqual(get_color("mp4"), "text-error")

    def test_audio_group_color(self):
        self.assertEqual(get_color("mp3"), "text-secondary")

    def test_archive_group_color(self):
        self.assertEqual(get_color("zip"), "text-warning")

    def test_default_color_for_unknown(self):
        self.assertEqual(get_color(""), "text-base-content/60")

    def test_pdf_override_color(self):
        self.assertEqual(get_color("pdf"), "text-error")

    def test_document_group_color(self):
        self.assertEqual(get_color("docx"), "text-base-content/60")


class ViewerResolutionTest(TestCase):
    def test_code_label_gets_text_viewer(self):
        self.assertEqual(get_viewer("python"), TextViewer)

    def test_text_label_gets_text_viewer(self):
        viewer = get_viewer("markdown")
        self.assertNotEqual(viewer, TextViewer)
        self.assertEqual(viewer, MarkdownViewer)

    def test_image_label_gets_image_viewer(self):
        self.assertEqual(get_viewer("jpeg"), ImageViewer)

    def test_png_gets_image_viewer(self):
        self.assertEqual(get_viewer("png"), ImageViewer)

    def test_pdf_gets_pdf_viewer(self):
        self.assertEqual(get_viewer("pdf"), PDFViewer)

    def test_video_gets_media_viewer(self):
        self.assertEqual(get_viewer("mp4"), MediaViewer)

    def test_audio_gets_media_viewer(self):
        self.assertEqual(get_viewer("mp3"), MediaViewer)

    def test_markdown_gets_markdown_viewer_not_text(self):
        """Label-specific match should beat group-level match."""
        viewer = get_viewer("markdown")
        self.assertEqual(viewer, MarkdownViewer)

    def test_unknown_label_gets_no_viewer(self):
        self.assertIsNone(get_viewer(""))

    def test_archive_gets_no_viewer(self):
        self.assertIsNone(get_viewer("zip"))

    def test_document_gets_no_viewer(self):
        self.assertIsNone(get_viewer("docx"))

    def test_css_gets_text_viewer(self):
        self.assertEqual(get_viewer("css"), TextViewer)

    def test_html_gets_text_viewer(self):
        self.assertEqual(get_viewer("html"), TextViewer)


class IsViewableTest(TestCase):
    def test_viewable_code(self):
        self.assertTrue(is_viewable("python"))

    def test_viewable_image(self):
        self.assertTrue(is_viewable("jpeg"))

    def test_viewable_pdf(self):
        self.assertTrue(is_viewable("pdf"))

    def test_viewable_video(self):
        self.assertTrue(is_viewable("mp4"))

    def test_not_viewable_archive(self):
        self.assertFalse(is_viewable("zip"))

    def test_not_viewable_unknown(self):
        self.assertFalse(is_viewable(""))


class GetInfoTest(TestCase):
    def test_returns_file_type_info_instance(self):
        info = get_info("python")
        self.assertIsInstance(info, FileTypeInfo)

    def test_python_info_complete(self):
        info = get_info("python")
        self.assertEqual(info.icon, "file-code")
        self.assertEqual(info.color, "text-info")
        self.assertEqual(info.group, "code")
        self.assertEqual(info.viewer, TextViewer)
        self.assertEqual(info.mime_type, "text/x-python")

    def test_jpeg_info_complete(self):
        info = get_info("jpeg")
        self.assertEqual(info.icon, "image")
        self.assertEqual(info.color, "text-success")
        self.assertEqual(info.group, "image")
        self.assertEqual(info.viewer, ImageViewer)
        self.assertEqual(info.mime_type, "image/jpeg")

    def test_pdf_info_complete(self):
        info = get_info("pdf")
        self.assertEqual(info.icon, "file-text")
        self.assertEqual(info.color, "text-error")
        self.assertEqual(info.group, "document")
        self.assertEqual(info.viewer, PDFViewer)
        self.assertEqual(info.mime_type, "application/pdf")

    def test_empty_label_defaults(self):
        info = get_info("")
        self.assertEqual(info.icon, "file")
        self.assertEqual(info.color, "text-base-content/60")
        self.assertEqual(info.group, "unknown")
        self.assertIsNone(info.viewer)
        self.assertEqual(info.mime_type, "application/octet-stream")

    def test_frozen_dataclass(self):
        info = get_info("python")
        with self.assertRaises(AttributeError):
            info.icon = "something-else"


class GetMimeTypeTest(TestCase):
    def test_python_mime(self):
        self.assertEqual(get_mime_type("python"), "text/x-python")

    def test_jpeg_mime(self):
        self.assertEqual(get_mime_type("jpeg"), "image/jpeg")

    def test_unknown_mime(self):
        self.assertEqual(get_mime_type(""), "application/octet-stream")

    def test_pdf_mime(self):
        self.assertEqual(get_mime_type("pdf"), "application/pdf")
