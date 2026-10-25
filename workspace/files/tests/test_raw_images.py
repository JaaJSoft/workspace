"""Camera RAW photos: detection, and decoding through the embedded preview."""

import io
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from PIL import ExifTags, Image, ImageOps

from workspace.files.services import FileService
from workspace.files.services.detection import (
    detect_from_bytes,
    detect_from_name,
    detect_from_stream,
)
from workspace.files.services.image_edits import render_edited
from workspace.files.services.raw_images import largest_jpeg, raw_label
from workspace.files.tests.rasters import (
    cr3_bytes,
    jpeg_bytes,
    raf_bytes,
    tiff_raw_bytes,
)
from workspace.photos.services.exif import read_metadata

User = get_user_model()


class RawDetectionTests(TestCase):
    def test_a_nef_is_a_nef_rather_than_a_tiff(self):
        result = detect_from_bytes(tiff_raw_bytes())

        self.assertEqual(
            (result.label, result.mime_type, result.group),
            ("nef", "image/x-nikon-nef", "image"),
        )

    def test_a_raw_is_detected_from_a_stream(self):
        self.assertEqual(detect_from_stream(io.BytesIO(cr3_bytes())).label, "cr3")

    def test_each_format_is_told_by_its_header(self):
        cases = {
            "dng": tiff_raw_bytes(make="Apple", dng=True, sub_ifds=False),
            "cr2": b"II*\x00\x10\x00\x00\x00CR\x02\x00" + bytes(64),
            "cr3": cr3_bytes(),
            "nef": tiff_raw_bytes(make="NIKON CORPORATION"),
            "arw": tiff_raw_bytes(make="SONY"),
            "orf": tiff_raw_bytes(make="OLYMPUS IMAGING CORP.", magic=b"IIRO"),
            "rw2": tiff_raw_bytes(make="Panasonic", magic=b"IIU\x00"),
            "pef": tiff_raw_bytes(make="RICOH IMAGING COMPANY, LTD."),
            "raf": raf_bytes(),
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertEqual(raw_label(data), label)

    def test_a_scanner_tiff_of_a_camera_brand_is_not_a_raw(self):
        # A Nikon Coolscan writes Make=NIKON too, but its picture is in IFD0.
        self.assertIsNone(raw_label(tiff_raw_bytes(sub_ifds=False)))

    def test_a_plain_tiff_still_opens_as_a_tiff(self):
        buf = io.BytesIO()
        Image.new("RGB", (40, 30), "red").save(buf, format="TIFF")

        self.assertEqual(detect_from_bytes(buf.getvalue()).label, "tiff")
        with Image.open(io.BytesIO(buf.getvalue())) as img:
            self.assertEqual((img.format, img.size), ("TIFF", (40, 30)))

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
    def test_opens_as_its_preview_rather_than_the_ifd0_thumbnail(self):
        with Image.open(io.BytesIO(tiff_raw_bytes(preview_size=(96, 64)))) as img:
            self.assertEqual(img.size, (96, 64))
            # The preview is red, the thumbnail blue.
            self.assertGreater(img.convert("RGB").getpixel((48, 32))[0], 150)

    def test_the_raw_orientation_turns_the_preview(self):
        data = tiff_raw_bytes(make="Apple", dng=True, orientation=6)

        with Image.open(io.BytesIO(data)) as img:
            self.assertEqual(ImageOps.exif_transpose(img).size, (64, 96))

    def test_metadata_comes_from_the_raw_header(self):
        metadata = read_metadata(io.BytesIO(tiff_raw_bytes(orientation=8)))

        self.assertEqual(metadata.camera_make, "NIKON CORPORATION")
        self.assertEqual(
            metadata.taken_at, datetime(2016, 11, 30, 13, 10, 53, tzinfo=UTC)
        )
        self.assertEqual((metadata.width, metadata.height), (64, 96))

    def test_a_cr3_reads_its_cmt_boxes(self):
        metadata = read_metadata(io.BytesIO(cr3_bytes()))

        self.assertEqual(metadata.camera_model, "Canon EOS R6")
        self.assertEqual(
            metadata.taken_at, datetime(2021, 5, 12, 12, 21, 21, tzinfo=UTC)
        )

    def test_a_raf_reads_its_preview_exif(self):
        exif = Image.Exif()
        exif[ExifTags.Base.Make] = "FUJIFILM"

        metadata = read_metadata(io.BytesIO(raf_bytes(exif=exif)))

        self.assertEqual(metadata.camera_make, "FUJIFILM")

    def test_a_jpeg_nested_in_the_preview_does_not_cut_it_short(self):
        # The EXIF thumbnail sits in the preview's APP1, ending on its own FFD9.
        thumbnail = jpeg_bytes(size=(16, 12))
        preview = jpeg_bytes(size=(96, 64))
        app1 = b"Exif\x00\x00" + thumbnail
        nested = (
            preview[:2]
            + b"\xff\xe1"
            + (len(app1) + 2).to_bytes(2, "big")
            + app1
            + preview[2:]
        )

        self.assertEqual(largest_jpeg(b"junk" + nested + b"junk"), nested)

    def test_an_edited_raw_downloads_without_its_file_structure(self):
        # A RAW's IFD0 and maker notes run past the 64 KB of a JPEG EXIF
        # segment; the download keeps what describes the photo.
        data = tiff_raw_bytes(extra={ExifTags.Base.StripOffsets: tuple(range(20_000))})
        user = User.objects.create_user(username="rawedit", password="x")
        file_obj = FileService.create_file(
            owner=user,
            name="DSC_0001.NEF",
            content=ContentFile(data, name="DSC_0001.NEF"),
        )
        file_obj.image_edit = {"rotation": 90}

        content, content_type, filename = render_edited(file_obj)

        self.assertEqual((content_type, filename), ("image/jpeg", "DSC_0001.jpg"))
        with Image.open(io.BytesIO(content)) as img:
            self.assertEqual(img.size, (64, 96))
            self.assertEqual(img.getexif()[ExifTags.Base.Make], "NIKON CORPORATION")
