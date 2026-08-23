from django.test import TestCase, override_settings

from workspace.files.services.filetype import (
    FileTypeInfo,
    get_color,
    get_group,
    get_icon,
    get_info,
    get_mime_type,
    get_viewer,
    get_viewer_slug,
    get_viewers,
    is_viewable,
    pin_viewer_for_upload,
)
from workspace.files.ui.viewers import (
    AudioViewer,
    ImageViewer,
    MarkdownViewer,
    PDFViewer,
    TextViewer,
    VideoViewer,
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

    def test_mp4_uses_video_viewer(self):
        self.assertEqual(get_viewer("mp4"), VideoViewer)

    def test_mp3_uses_audio_viewer(self):
        self.assertEqual(get_viewer("mp3"), AudioViewer)

    def test_markdown_gets_markdown_viewer_not_text(self):
        """Label-specific match should beat group-level match."""
        viewer = get_viewer("markdown")
        self.assertEqual(viewer, MarkdownViewer)

    def test_unknown_label_gets_no_viewer(self):
        self.assertIsNone(get_viewer(""))

    def test_archive_gets_no_viewer(self):
        self.assertIsNone(get_viewer("zip"))

    def test_document_gets_no_viewer_without_a_wopi_editor(self):
        with override_settings(WOPI_DISCOVERY_URL=""):
            self.assertIsNone(get_viewer("docx"))

    def test_office_labels_get_office_viewer_with_a_wopi_editor(self):
        from workspace.files.ui.viewers import OfficeViewer

        with override_settings(WOPI_DISCOVERY_URL="https://editor/hosting/discovery"):
            for label in ("docx", "xlsx", "pptx", "odt", "ods", "odp"):
                self.assertEqual(get_viewer(label), OfficeViewer, label)

    def test_office_extension_upgrades_generic_label_with_a_wopi_editor(self):
        from workspace.files.ui.viewers import OfficeViewer

        with override_settings(WOPI_DISCOVERY_URL="https://editor/hosting/discovery"):
            self.assertEqual(get_viewer("unknown", "report.docx"), OfficeViewer)

    def test_office_pinned_slug_degrades_when_wopi_is_off(self):
        from workspace.files.services.filetype import get_viewer_by_slug

        with override_settings(WOPI_DISCOVERY_URL=""):
            self.assertIsNone(get_viewer_by_slug("office"))

    def test_css_gets_text_viewer(self):
        self.assertEqual(get_viewer("css"), TextViewer)

    def test_html_gets_text_viewer(self):
        self.assertEqual(get_viewer("html"), TextViewer)


class ExtensionAwareViewerResolutionTest(TestCase):
    """Viewers resolve from the content label AND the filename extension.

    Magika is content-based, so a sparse Markdown file is often detected as
    plain ``txt``. The ``.md`` extension must upgrade it to the MarkdownViewer
    instead of falling back to the generic TextViewer.
    """

    def test_markdown_extension_upgrades_plaintext_to_markdown_viewer(self):
        self.assertEqual(get_viewer("txt", "notes.md"), MarkdownViewer)

    def test_markdown_long_extension_also_upgrades(self):
        self.assertEqual(get_viewer("txt", "notes.markdown"), MarkdownViewer)

    def test_content_label_wins_over_misleading_extension(self):
        """A PNG renamed to .txt must still open in the ImageViewer."""
        self.assertEqual(get_viewer("png", "photo.txt"), ImageViewer)

    def test_specific_content_label_beats_extension(self):
        """When content is detected as markdown, the .md extension agrees."""
        self.assertEqual(get_viewer("markdown", "notes.md"), MarkdownViewer)

    def test_extension_matching_content_keeps_text_viewer(self):
        self.assertEqual(get_viewer("txt", "notes.txt"), TextViewer)

    def test_extension_rescues_unknown_content(self):
        """Unrecognised content with a known extension still finds a viewer."""
        self.assertEqual(get_viewer("unknown", "readme.md"), MarkdownViewer)

    def test_no_name_falls_back_to_content_label(self):
        self.assertEqual(get_viewer("txt"), TextViewer)

    def test_is_viewable_uses_extension(self):
        self.assertTrue(is_viewable("unknown", "readme.md"))


class ExtensionlessMarkdownTest(TestCase):
    """The WYSIWYG MarkdownViewer (Milkdown Crepe) crashes on content it was
    never authored to handle. A file detected as ``markdown`` by content but
    with no extension is treated as plain text instead, which renders safely.
    """

    def test_extensionless_markdown_falls_back_to_text_viewer(self):
        self.assertEqual(get_viewer("markdown", "mynotes"), TextViewer)

    def test_extensionless_markdown_is_still_viewable(self):
        self.assertTrue(is_viewable("markdown", "mynotes"))

    def test_markdown_with_md_extension_keeps_markdown_viewer(self):
        self.assertEqual(get_viewer("markdown", "notes.md"), MarkdownViewer)

    def test_markdown_label_without_name_keeps_markdown_viewer(self):
        """Label-only callers (no filename) keep the markdown viewer."""
        self.assertEqual(get_viewer("markdown"), MarkdownViewer)

    def test_md_extension_still_upgrades_plaintext(self):
        """The extension upgrade from the previous fix must keep working."""
        self.assertEqual(get_viewer("txt", "notes.md"), MarkdownViewer)


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


class ViewerSlugAndPinningTest(TestCase):
    def test_every_viewer_declares_a_slug(self):
        """The slug is persisted in the `viewer` override columns, so a viewer
        without one could never be pinned."""
        from workspace.files.ui.viewers import BaseViewer

        for viewer_cls in BaseViewer.__subclasses__():
            self.assertTrue(viewer_cls.slug, f"{viewer_cls.__name__} has no slug")

    def test_slugs_are_unique(self):
        from workspace.files.ui.viewers import BaseViewer

        slugs = [v.slug for v in BaseViewer.__subclasses__()]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_audio_and_video_viewers_are_direct_subclasses(self):
        """Viewer resolution only walks direct subclasses of BaseViewer, so an
        intermediate shared base would drop both from the registry."""
        from workspace.files.ui.viewers import BaseViewer

        subclasses = BaseViewer.__subclasses__()
        self.assertIn(AudioViewer, subclasses)
        self.assertIn(VideoViewer, subclasses)

    def test_get_viewer_slug_derives_from_the_content_label(self):
        self.assertEqual(get_viewer_slug("mp3"), "audio")
        self.assertEqual(get_viewer_slug("mp4"), "video")

    def test_get_viewer_slug_is_empty_when_nothing_handles_the_label(self):
        self.assertEqual(get_viewer_slug("unknown"), "")

    def test_get_viewers_returns_the_winner_first(self):
        viewers = get_viewers("mp3")
        self.assertEqual(viewers[0], get_viewer("mp3"))

    def test_get_viewers_is_empty_when_nothing_handles_the_label(self):
        self.assertEqual(get_viewers("unknown"), [])

    def test_pin_audio_only_webm(self):
        """MediaRecorder emits audio/webm; Magika sees only the container."""
        self.assertEqual(
            pin_viewer_for_upload("webm", "audio/webm;codecs=opus"), "audio"
        )

    def test_pin_audio_only_mp4(self):
        """Safari's MediaRecorder path."""
        self.assertEqual(pin_viewer_for_upload("mp4", "audio/mp4"), "audio")

    def test_no_pin_when_the_declared_type_is_video(self):
        self.assertEqual(pin_viewer_for_upload("webm", "video/webm"), "")

    def test_no_pin_for_a_non_container_label(self):
        """The guard: lying about Content-Type cannot pin a viewer on arbitrary
        content. Only labels Magika confidently read as media containers are
        eligible."""
        self.assertEqual(pin_viewer_for_upload("html", "audio/webm"), "")
        self.assertEqual(pin_viewer_for_upload("png", "audio/webm"), "")

    def test_no_pin_without_a_declared_type(self):
        self.assertEqual(pin_viewer_for_upload("webm", None), "")
        self.assertEqual(pin_viewer_for_upload("webm", ""), "")

    def test_no_pin_for_ogg(self):
        """The KB already groups ogg as audio, so it needs no pin."""
        self.assertEqual(pin_viewer_for_upload("ogg", "audio/ogg"), "")
