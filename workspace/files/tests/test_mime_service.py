from django.core.cache import cache
from django.test import TestCase

from workspace.files.models import MimeTypeRule
from workspace.files.services import mime as mime_service
from workspace.files.utils import FileTypeDetector, FileCategory
from workspace.files.ui.viewers import (
    ViewerRegistry, TextViewer, ImageViewer, MarkdownViewer, PDFViewer, MediaViewer,
)


class MimeTypeRuleModelTest(TestCase):
    def test_is_wildcard_auto_set_true(self):
        rule = MimeTypeRule.objects.create(
            pattern="test/*", priority=1000,
            icon="file", color="text-info", category="unknown",
        )
        self.assertTrue(rule.is_wildcard)

    def test_is_wildcard_auto_set_false(self):
        rule = MimeTypeRule.objects.create(
            pattern="text/plain-test", priority=10,
            icon="file", color="text-info", category="text",
        )
        self.assertFalse(rule.is_wildcard)

    def test_pattern_unique(self):
        MimeTypeRule.objects.create(
            pattern="unique/type", priority=10,
            icon="file", color="text-info", category="unknown",
        )
        with self.assertRaises(Exception):
            MimeTypeRule.objects.create(
                pattern="unique/type", priority=20,
                icon="file", color="text-info", category="unknown",
            )


class MimeTypeServiceCacheTest(TestCase):
    def setUp(self):
        mime_service.invalidate_cache()

    def test_cold_cache_builds(self):
        self.assertIsNone(cache.get(mime_service.CACHE_KEY))
        mime_service.get_rule("text/plain")
        self.assertIsNotNone(cache.get(mime_service.CACHE_KEY))

    def test_warm_cache_zero_queries(self):
        # warm the cache
        mime_service.get_rule("text/plain")
        with self.assertNumQueries(0):
            mime_service.get_rule("text/plain")
            mime_service.get_rule("image/png")
            mime_service.get_rule("application/pdf")

    def test_invalidation_clears_cache(self):
        mime_service.get_rule("text/plain")
        self.assertIsNotNone(cache.get(mime_service.CACHE_KEY))
        mime_service.invalidate_cache()
        self.assertIsNone(cache.get(mime_service.CACHE_KEY))

    def test_signal_invalidation_on_save(self):
        mime_service.get_rule("text/plain")
        self.assertIsNotNone(cache.get(mime_service.CACHE_KEY))
        MimeTypeRule.objects.create(
            pattern="application/x-test", priority=10,
            icon="file", color="text-info", category="unknown",
        )
        self.assertIsNone(cache.get(mime_service.CACHE_KEY))

    def test_signal_invalidation_on_delete(self):
        rule = MimeTypeRule.objects.create(
            pattern="application/x-todelete", priority=10,
            icon="file", color="text-info", category="unknown",
        )
        mime_service.get_rule("text/plain")
        self.assertIsNotNone(cache.get(mime_service.CACHE_KEY))
        rule.delete()
        self.assertIsNone(cache.get(mime_service.CACHE_KEY))


class MimeTypeServiceLookupTest(TestCase):
    def setUp(self):
        mime_service.invalidate_cache()

    def test_exact_match(self):
        self.assertEqual(mime_service.get_icon("application/pdf"), "file-text")
        self.assertEqual(mime_service.get_color("application/pdf"), "text-error")
        self.assertEqual(mime_service.get_category("application/pdf"), "pdf")
        self.assertEqual(mime_service.get_viewer_type("application/pdf"), "pdf")

    def test_wildcard_match(self):
        # text/x-unknown is not an exact rule but matches text/*
        self.assertEqual(mime_service.get_icon("text/x-unknown"), "file-text")
        self.assertEqual(mime_service.get_category("text/x-unknown"), "text")

    def test_exact_takes_priority_over_wildcard(self):
        self.assertEqual(mime_service.get_icon("text/html"), "file-code")
        # wildcard text/* would give "file-text"

    def test_unknown_returns_default(self):
        self.assertEqual(mime_service.get_icon("application/octet-stream"), "file")
        self.assertEqual(mime_service.get_color("application/octet-stream"), "text-base-content/60")
        self.assertEqual(mime_service.get_category("application/octet-stream"), "unknown")
        self.assertIsNone(mime_service.get_viewer_type("application/octet-stream"))

    def test_empty_returns_default(self):
        self.assertEqual(mime_service.get_icon(""), "file")
        self.assertEqual(mime_service.get_icon(None), "file")

    def test_case_insensitive(self):
        self.assertEqual(mime_service.get_icon("Application/PDF"), "file-text")
        self.assertEqual(mime_service.get_icon("TEXT/PLAIN"), "file-text")

    def test_is_viewable(self):
        self.assertTrue(mime_service.is_viewable("text/plain"))
        self.assertTrue(mime_service.is_viewable("image/png"))
        self.assertTrue(mime_service.is_viewable("application/pdf"))
        self.assertTrue(mime_service.is_viewable("video/mp4"))
        self.assertTrue(mime_service.is_viewable("audio/mpeg"))
        self.assertFalse(mime_service.is_viewable("application/zip"))
        self.assertFalse(mime_service.is_viewable("application/octet-stream"))
        self.assertFalse(mime_service.is_viewable(""))

    def test_archives_have_no_viewer(self):
        for mime in ("application/zip", "application/x-tar", "application/gzip", "application/x-rar-compressed"):
            self.assertIsNone(mime_service.get_viewer_type(mime), msg=mime)
            self.assertEqual(mime_service.get_icon(mime), "file-archive", msg=mime)
            self.assertEqual(mime_service.get_color(mime), "text-warning", msg=mime)


class BackwardCompatTemplateFiltersTest(TestCase):
    def setUp(self):
        mime_service.invalidate_cache()

    def test_mime_to_lucide_filter(self):
        from workspace.files.templatetags.file_filters import mime_to_lucide
        self.assertEqual(mime_to_lucide("image/png"), "image")
        self.assertEqual(mime_to_lucide("video/mp4"), "video")
        self.assertEqual(mime_to_lucide("audio/mpeg"), "music")
        self.assertEqual(mime_to_lucide("application/pdf"), "file-text")
        self.assertEqual(mime_to_lucide("application/json"), "file-json")
        self.assertEqual(mime_to_lucide("application/zip"), "file-archive")
        self.assertEqual(mime_to_lucide(""), "file")
        self.assertEqual(mime_to_lucide(None), "file")

    def test_mime_to_color_filter(self):
        from workspace.files.templatetags.file_filters import mime_to_color
        self.assertEqual(mime_to_color("image/png"), "text-success")
        self.assertEqual(mime_to_color("video/mp4"), "text-error")
        self.assertEqual(mime_to_color("audio/mpeg"), "text-secondary")
        self.assertEqual(mime_to_color("application/pdf"), "text-error")
        self.assertEqual(mime_to_color("application/zip"), "text-warning")
        self.assertEqual(mime_to_color("text/plain"), "text-info")
        self.assertEqual(mime_to_color(""), "text-base-content/60")
        self.assertEqual(mime_to_color(None), "text-base-content/60")


class BackwardCompatFileTypeDetectorTest(TestCase):
    def setUp(self):
        mime_service.invalidate_cache()

    def test_categorize(self):
        self.assertEqual(FileTypeDetector.categorize("text/plain"), FileCategory.TEXT)
        self.assertEqual(FileTypeDetector.categorize("image/png"), FileCategory.IMAGE)
        self.assertEqual(FileTypeDetector.categorize("application/pdf"), FileCategory.PDF)
        self.assertEqual(FileTypeDetector.categorize("video/mp4"), FileCategory.VIDEO)
        self.assertEqual(FileTypeDetector.categorize("audio/mpeg"), FileCategory.AUDIO)
        self.assertEqual(FileTypeDetector.categorize("application/octet-stream"), FileCategory.UNKNOWN)
        self.assertEqual(FileTypeDetector.categorize(""), FileCategory.UNKNOWN)

    def test_is_viewable(self):
        self.assertTrue(FileTypeDetector.is_viewable("text/plain"))
        self.assertTrue(FileTypeDetector.is_viewable("image/png"))
        self.assertFalse(FileTypeDetector.is_viewable("application/zip"))
        self.assertFalse(FileTypeDetector.is_viewable(""))

    def test_text_wildcard_categorize(self):
        # text/x-unknown should still be TEXT via wildcard
        self.assertEqual(FileTypeDetector.categorize("text/x-unknown"), FileCategory.TEXT)


class BackwardCompatViewerRegistryTest(TestCase):
    def setUp(self):
        mime_service.invalidate_cache()

    def test_text_viewer(self):
        self.assertEqual(ViewerRegistry.get_viewer("text/plain"), TextViewer)
        self.assertEqual(ViewerRegistry.get_viewer("text/html"), TextViewer)
        self.assertEqual(ViewerRegistry.get_viewer("application/json"), TextViewer)

    def test_markdown_viewer(self):
        self.assertEqual(ViewerRegistry.get_viewer("text/markdown"), MarkdownViewer)

    def test_image_viewer(self):
        self.assertEqual(ViewerRegistry.get_viewer("image/png"), ImageViewer)
        self.assertEqual(ViewerRegistry.get_viewer("image/jpeg"), ImageViewer)

    def test_pdf_viewer(self):
        self.assertEqual(ViewerRegistry.get_viewer("application/pdf"), PDFViewer)

    def test_media_viewer(self):
        self.assertEqual(ViewerRegistry.get_viewer("video/mp4"), MediaViewer)
        self.assertEqual(ViewerRegistry.get_viewer("audio/mpeg"), MediaViewer)

    def test_unsupported(self):
        self.assertIsNone(ViewerRegistry.get_viewer("application/zip"))
        self.assertIsNone(ViewerRegistry.get_viewer(""))

    def test_image_x_icon_fix(self):
        """image/x-icon should now return ImageViewer (was missing before)."""
        self.assertEqual(ViewerRegistry.get_viewer("image/x-icon"), ImageViewer)

    def test_wildcard_fallback(self):
        # unknown image sub-type falls back to image/* wildcard
        self.assertEqual(ViewerRegistry.get_viewer("image/x-unknown"), ImageViewer)
        # unknown text sub-type falls back to text/*
        self.assertEqual(ViewerRegistry.get_viewer("text/x-whatever"), TextViewer)

    def test_is_supported(self):
        self.assertTrue(ViewerRegistry.is_supported("text/plain"))
        self.assertFalse(ViewerRegistry.is_supported("application/zip"))
