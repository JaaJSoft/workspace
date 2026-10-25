"""The position blanked out of each container, in place and at the same size."""

import io
import json
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

import pillow_heif
from django.test import SimpleTestCase
from PIL import ExifTags, Image

from workspace.files.tests.videos import requires_ffmpeg
from workspace.photos.services.gps_redaction import (
    PatchedFile,
    location_patches,
)

pillow_heif.register_heif_opener()

GPS = {
    ExifTags.GPS.GPSLatitudeRef: "N",
    ExifTags.GPS.GPSLatitude: (48.0, 51.0, 30.24),
    ExifTags.GPS.GPSLongitudeRef: "E",
    ExifTags.GPS.GPSLongitude: (2.0, 17.0, 40.2),
    ExifTags.GPS.GPSAltitude: 35.0,
}
XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description xmlns:exif="http://ns.adobe.com/exif/1.0/" '
    b'xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
    b'exif:GPSLatitude="48,51.504N" xmp:Rating="5">'
    b"<exif:GPSLongitude>2,17.67E</exif:GPSLongitude>"
    b"</rdf:Description></rdf:RDF></x:xmpmeta>"
)


def _exif():
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Apple"
    exif.get_ifd(ExifTags.IFD.GPSInfo).update(GPS)
    return exif


def _picture(size=(64, 48), color=(200, 60, 40)):
    return Image.new("RGB", size, color)


def _encode(format, **options):
    buf = io.BytesIO()
    _picture().save(buf, format=format, exif=_exif(), **options)
    return buf.getvalue()


def _redacted(data):
    return PatchedFile(io.BytesIO(data), location_patches(io.BytesIO(data))).read()


class ImageContainerTests(SimpleTestCase):
    def assert_redacted(self, data, *, xmp=False):
        self.assertTrue(Image.open(io.BytesIO(data)).getexif().get_ifd(0x8825))

        served = _redacted(data)

        self.assertEqual(len(served), len(data))
        image = Image.open(io.BytesIO(served))
        exif = image.getexif()
        self.assertEqual(dict(exif.get_ifd(ExifTags.IFD.GPSInfo)), {})
        self.assertEqual(exif.get(ExifTags.Base.Make), "Apple")
        self.assertEqual(
            image.convert("RGB").tobytes(),
            Image.open(io.BytesIO(data)).convert("RGB").tobytes(),
        )
        if xmp:
            self.assertNotIn(b"51.504", served)
            self.assertNotIn(b"17.67", served)
            self.assertIn(b'xmp:Rating="5"', served)
        return served

    def test_jpeg(self):
        self.assert_redacted(_encode("JPEG", xmp=XMP), xmp=True)

    def test_xmp_values_with_attributes_or_nested_in_rdf(self):
        xmp = (
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
            b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            b'<rdf:Description xmlns:exif="http://ns.adobe.com/exif/1.0/">'
            b'<exif:GPSLatitude xml:lang="x-default">48,51.504N</exif:GPSLatitude>'
            b"<exif:GPSLongitude><rdf:Alt><rdf:li>2,17.67E</rdf:li></rdf:Alt>"
            b"</exif:GPSLongitude><exif:GPSVersionID/>"
            b"</rdf:Description></rdf:RDF></x:xmpmeta>"
        )

        served = _redacted(_encode("JPEG", xmp=xmp))

        self.assertNotIn(b"51.504", served)
        self.assertNotIn(b"17.67", served)
        packet = served[served.index(b"<x:xmpmeta") :]
        ElementTree.fromstring(packet[: packet.index(b"</x:xmpmeta>") + 12])

    def test_png_keeps_a_valid_chunk_crc(self):
        served = self.assert_redacted(_encode("PNG"))

        start = served.index(b"eXIf") - 4
        (length,) = struct.unpack(">I", served[start : start + 4])
        chunk = served[start + 4 : start + 8 + length]
        (crc,) = struct.unpack(">I", served[start + 8 + length : start + 12 + length])
        self.assertEqual(zlib.crc32(chunk) & 0xFFFFFFFF, crc)

    def test_webp(self):
        self.assert_redacted(_encode("WEBP", xmp=XMP, lossless=True), xmp=True)

    def test_heic(self):
        self.assert_redacted(_encode("HEIF", quality=-1))

    def test_avif(self):
        self.assert_redacted(_encode("AVIF"))

    def test_the_secondary_images_of_a_multi_picture_jpeg(self):
        # A phone's depth map or preview is a JPEG of its own after the
        # first, with its own EXIF.
        second = _picture((32, 24), (1, 2, 3))
        second.info["exif"] = _exif().tobytes()
        buf = io.BytesIO()
        _picture().save(
            buf, format="MPO", save_all=True, append_images=[second], exif=_exif()
        )
        data = buf.getvalue()

        served = _redacted(data)

        image = Image.open(io.BytesIO(served))
        for frame in range(image.n_frames):
            image.seek(frame)
            self.assertEqual(dict(image.getexif().get_ifd(ExifTags.IFD.GPSInfo)), {})

    def test_a_photo_without_a_position_is_left_alone(self):
        buf = io.BytesIO()
        _picture().save(buf, format="JPEG")

        self.assertEqual(location_patches(io.BytesIO(buf.getvalue())), [])

    def test_a_truncated_file_does_not_fail(self):
        for format in ("JPEG", "PNG", "WEBP", "HEIF"):
            data = _encode(format)
            for cut in (12, 40, len(data) // 2):
                with self.subTest(format=format, cut=cut):
                    location_patches(io.BytesIO(data[:cut]))

    def test_other_files_are_left_alone(self):
        for data in (b"", b"%PDF-1.7\n", b"PK\x03\x04" + bytes(40)):
            self.assertEqual(location_patches(io.BytesIO(data)), [])


class PatchedFileTests(SimpleTestCase):
    def test_a_read_across_a_patch_boundary(self):
        data = bytes(range(100))
        patched = PatchedFile(io.BytesIO(data), [(10, b"\xff" * 5), (50, b"\xee")])

        patched.seek(12)
        self.assertEqual(patched.read(10), b"\xff" * 3 + bytes(range(15, 22)))
        patched.seek(0)
        whole = patched.read()
        self.assertEqual(len(whole), 100)
        self.assertEqual(whole[50], 0xEE)
        self.assertEqual(whole[49], 49)


def _video(extension, *metadata, flags=()):
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / f"clip{extension}"
        subprocess.run(
            [
                shutil.which("ffmpeg"),
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=64x48:d=1",
                "-c:v",
                "mpeg4",
                *flags,
                *(arg for tag in metadata for arg in ("-metadata", tag)),
                str(target),
            ],
            check=True,
            capture_output=True,
        )
        return target.read_bytes()


def _tags(data):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "clip.mp4"
        path.write_bytes(data)
        output = subprocess.run(
            [shutil.which("ffprobe"), "-v", "error", "-show_format", "-of", "json"]
            + [str(path)],
            check=True,
            capture_output=True,
        ).stdout
    return json.loads(output)["format"].get("tags", {})


@requires_ffmpeg
class VideoTests(SimpleTestCase):
    ISO6709 = "+48.8584+002.2945+035.000/"

    def assert_no_location(self, data):
        served = _redacted(data)

        self.assertEqual(len(served), len(data))
        self.assertNotIn(b"48.8584", served)
        tags = _tags(served)
        for key, value in tags.items():
            if "location" in key:
                self.assertEqual(value.strip(), "", key)
        return tags

    def test_mp4(self):
        data = _video(".mp4", f"location={self.ISO6709}")
        self.assertTrue(_tags(data)["location"].startswith("+48.8584+002.2945"))

        self.assert_no_location(data)

    def test_mp4_with_its_moov_up_front(self):
        self.assert_no_location(
            _video(
                ".mp4", f"location={self.ISO6709}", flags=("-movflags", "+faststart")
            )
        )

    def test_quicktime(self):
        self.assert_no_location(_video(".mov", f"location={self.ISO6709}"))

    def test_apple_location_key(self):
        tags = self.assert_no_location(
            _video(
                ".mov",
                f"com.apple.quicktime.location.ISO6709={self.ISO6709}",
                "com.apple.quicktime.make=Apple",
                flags=("-movflags", "use_metadata_tags"),
            )
        )

        self.assertEqual(tags["com.apple.quicktime.make"], "Apple")
