"""Camera RAW photos: detection, and decoding through LibRaw's preview."""

import io
from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import TemporaryUploadedFile
from django.test import TestCase
from PIL import ExifTags, Image, ImageOps, UnidentifiedImageError

from workspace.files.services import FileService, raw_images
from workspace.files.services.detection import detect_from_name, detect_from_stream
from workspace.files.services.image_edits import render_edited
from workspace.files.tests.rasters import dng_bytes
from workspace.photos.services.exif import read_metadata

User = get_user_model()


def _image(fmt):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buffer, format=fmt)
    return buffer.getvalue()


class RawDetectionTests(TestCase):
    def test_a_dng_is_a_dng_rather_than_a_tiff(self):
        result = detect_from_stream(io.BytesIO(dng_bytes()), "IMG_0001.DNG")

        self.assertEqual(
            (result.label, result.mime_type, result.group),
            ("dng", "image/x-adobe-dng", "image"),
        )

    def test_the_stream_is_left_where_it_was(self):
        stream = io.BytesIO(dng_bytes())

        detect_from_stream(stream, "IMG_0001.DNG")

        self.assertEqual(stream.tell(), 0)

    def test_an_upload_spooled_to_disk_is_read_from_its_path(self):
        upload = TemporaryUploadedFile("IMG_0001.DNG", "", 0, None)
        upload.write(dng_bytes())
        upload.seek(0)
        self.addCleanup(upload.close)

        self.assertEqual(detect_from_stream(upload, upload.name).label, "dng")

    def test_a_raw_extension_on_another_image_is_not_trusted(self):
        for fmt, label in (("TIFF", "tiff"), ("JPEG", "jpeg")):
            with self.subTest(fmt):
                result = detect_from_stream(io.BytesIO(_image(fmt)), "scan.nef")
                self.assertEqual(result.label, label)

    def test_raw_extensions_are_known_by_name(self):
        for name, label in (
            ("DSC_0001.NEF", "nef"),
            ("IMG_0001.dng", "dng"),
            ("_MG_0001.CR3", "cr3"),
            ("DSCF0001.RAF", "raf"),
        ):
            with self.subTest(name):
                result = detect_from_name(name)
                self.assertEqual((result.label, result.group), (label, "image"))


class RawDecodingTests(TestCase):
    def test_opens_as_its_embedded_preview(self):
        with Image.open(io.BytesIO(dng_bytes(preview_size=(96, 64)))) as img:
            self.assertEqual((img.format, img.size), ("RAW", (96, 64)))

    def test_the_raw_orientation_turns_the_preview(self):
        with Image.open(io.BytesIO(dng_bytes(orientation=6))) as img:
            self.assertEqual(ImageOps.exif_transpose(img).size, (64, 96))

    def test_metadata_comes_from_the_raw(self):
        metadata = read_metadata(io.BytesIO(dng_bytes(orientation=8)))

        self.assertEqual(
            (metadata.camera_make, metadata.camera_model), ("Canon", "EOS R6")
        )
        self.assertEqual(
            metadata.taken_at, datetime(2021, 5, 12, 12, 21, 21, tzinfo=UTC)
        )
        self.assertEqual((metadata.width, metadata.height), (64, 96))

    def test_other_images_still_open_with_their_own_plugin(self):
        for fmt in ("TIFF", "JPEG", "PNG"):
            with self.subTest(fmt), Image.open(io.BytesIO(_image(fmt))) as img:
                self.assertEqual((img.format, img.size), (fmt, (40, 30)))

    def test_bytes_no_plugin_reads_are_still_unidentified(self):
        with self.assertRaises(UnidentifiedImageError):
            Image.open(io.BytesIO(b"not an image" * 100))

    def test_an_edited_raw_downloads_as_a_jpeg_with_its_exif(self):
        user = User.objects.create_user(username="rawedit", password="x")
        file_obj = FileService.create_file(
            owner=user,
            name="IMG_0001.DNG",
            content=ContentFile(dng_bytes(), name="IMG_0001.DNG"),
        )
        file_obj.image_edit = {"rotation": 90}

        content, content_type, filename = render_edited(file_obj)

        self.assertEqual((content_type, filename), ("image/jpeg", "IMG_0001.jpg"))
        with Image.open(io.BytesIO(content)) as img:
            self.assertEqual(img.size, (64, 96))
            self.assertEqual(img.getexif()[ExifTags.Base.Make], "Canon")


class RawSizeCapTests(TestCase):
    """LibRaw reads from memory: a file past the cap is never read whole."""

    def setUp(self):
        self.data = dng_bytes()
        patcher = patch.object(raw_images, "MAX_RAW_SIZE", len(self.data) - 1)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_it_is_not_detected_as_a_raw(self):
        result = detect_from_stream(io.BytesIO(self.data), "IMG_0001.DNG")

        self.assertNotEqual(result.label, "dng")

    def test_libraw_does_not_open_it(self):
        # Pillow's TIFF plugin, next in line, opens the DNG's first directory.
        with Image.open(io.BytesIO(self.data)) as img:
            self.assertNotEqual(img.format, "RAW")
