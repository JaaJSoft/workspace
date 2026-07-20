"""Output-behavior tests for the thumbnail generation service.

These pin the observable result of generate_thumbnail (format, size,
aspect-ratio, alpha handling) so the draft-mode decoding optimization can be
made with a safety net - the optimization must not change any of these.
"""

import io
import logging

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from PIL import Image

from workspace.files.services import FileService
from workspace.files.services.thumbnails import (
    THUMBNAIL_MAX_SIZE,
    generate_thumbnail,
    get_thumbnail_path,
)

User = get_user_model()
logger = logging.getLogger(__name__)


def _image_bytes(mode, size, fmt, color):
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


class GenerateThumbnailOutputTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="thumbgen", password="p")

    def _make_file(self, name, data, ftype, mime):
        f = FileService.create_file(
            owner=self.user,
            name=name,
            content=ContentFile(data, name=name),
            mime_type=mime,
        )
        f.type = ftype
        f.save(update_fields=["type"])
        self.addCleanup(self._cleanup_thumb, f.uuid)
        return f

    def _cleanup_thumb(self, uuid):
        path = get_thumbnail_path(uuid)
        try:
            if default_storage.exists(path):
                default_storage.delete(path)
        except (PermissionError, OSError):
            # Thumbnail cleanup is best-effort: a blocked or unavailable
            # delete (e.g. Windows file lock) must not fail the test run.
            logger.debug("could not delete test thumbnail %s", uuid)

    def _open_thumb(self, uuid):
        path = get_thumbnail_path(uuid)
        self.assertTrue(default_storage.exists(path), "thumbnail was not written")
        with default_storage.open(path, "rb") as fh:
            data = fh.read()
        img = Image.open(io.BytesIO(data))
        img.load()
        return img

    def test_large_jpeg_capped_to_max_size_as_webp(self):
        # A source larger than the box exercises the downscale path (and the
        # JPEG draft-mode decode). 2000x1500 fits to 512x384 keeping aspect.
        data = _image_bytes("RGB", (2000, 1500), "JPEG", (10, 120, 200))
        f = self._make_file("big.jpg", data, "jpeg", "image/jpeg")

        self.assertTrue(generate_thumbnail(f))

        img = self._open_thumb(f.uuid)
        self.assertEqual(img.format, "WEBP")
        self.assertLessEqual(max(img.size), max(THUMBNAIL_MAX_SIZE))
        self.assertEqual(img.size, (512, 384))

    def test_landscape_png_aspect_ratio_preserved(self):
        data = _image_bytes("RGB", (1000, 500), "PNG", (0, 80, 0))
        f = self._make_file("wide.png", data, "png", "image/png")

        self.assertTrue(generate_thumbnail(f))

        img = self._open_thumb(f.uuid)
        self.assertEqual(img.size, (512, 256))

    def test_rgba_png_flattened_to_rgb(self):
        data = _image_bytes("RGBA", (400, 400), "PNG", (255, 0, 0, 128))
        f = self._make_file("alpha.png", data, "png", "image/png")

        self.assertTrue(generate_thumbnail(f))

        img = self._open_thumb(f.uuid)
        # Alpha is composited onto a white background, so the WebP is RGB.
        self.assertEqual(img.mode, "RGB")

    def test_svg_rasterized_to_square_webp(self):
        # cairosvg letterboxes a non-square SVG into the requested square box;
        # the thumbnail is a 512x512 WebP with the artwork centered. This pins
        # that behavior (so a future "tighten SVG dims" change is a conscious one).
        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 100">'
            b'<rect width="400" height="100" fill="red"/></svg>'
        )
        f = self._make_file("logo.svg", svg, "svg", "image/svg+xml")

        self.assertTrue(generate_thumbnail(f))

        img = self._open_thumb(f.uuid)
        self.assertEqual(img.format, "WEBP")
        self.assertEqual(img.size, THUMBNAIL_MAX_SIZE)
