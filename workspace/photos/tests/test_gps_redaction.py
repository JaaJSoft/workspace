"""The position blanked out of each container, in place and at the same size."""

import io
import json
import shutil
import struct
import subprocess
import tempfile
import time
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

    def test_unclosed_gps_elements_take_linear_time(self):
        crafted = b"<exif:GPSLatitude>" * 120_000
        data = _encode("WEBP", xmp=crafted, lossless=True)

        started = time.monotonic()
        location_patches(io.BytesIO(data))

        self.assertLess(time.monotonic() - started, 2)

    def test_xmp_without_any_size_limit(self):
        elements = b"".join(
            b"<exif:GPSDest%d>9%dN</exif:GPSDest%d>" % (n, n, n) for n in range(400)
        )
        xmp = (
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
            b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            b'<rdf:Description xmlns:exif="http://ns.adobe.com/exif/1.0/">'
            + elements
            + b"<exif:GPSLatitude>"
            + b" " * 5000
            + b"48,51.504N</exif:GPSLatitude>"
            b"</rdf:Description></rdf:RDF></x:xmpmeta>"
        )

        served = _redacted(_encode("WEBP", xmp=xmp, lossless=True))

        self.assertNotIn(b"51.504", served)
        self.assertNotIn(b"9399N", served)

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


def _box(kind, payload):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _gps_tiff(**tags):
    """A little-endian TIFF block whose IFD0 points at a GPS IFD."""
    exif = _exif()
    for tag, value in tags.items():
        exif[getattr(ExifTags.Base, tag)] = value
    return exif.tobytes()[6:]


def _read_exif(tiff):
    exif = Image.Exif()
    exif.load(b"Exif\x00\x00" + tiff)
    return exif


def _located(tiff):
    return bool(_read_exif(tiff).get_ifd(ExifTags.IFD.GPSInfo))


def _png_crcs_hold(data):
    position = 8
    while position < len(data):
        (length,) = struct.unpack(">I", data[position : position + 4])
        chunk = data[position + 4 : position + 8 + length]
        (crc,) = struct.unpack(
            ">I", data[position + 8 + length : position + 12 + length]
        )
        if zlib.crc32(chunk) & 0xFFFFFFFF != crc:
            return False
        position += 12 + length
    return True


class RawTests(SimpleTestCase):
    def test_dng(self):
        from workspace.files.tests.rasters import dng_bytes

        data = dng_bytes(gps=(48, 2))
        self.assertTrue(Image.open(io.BytesIO(data)).getexif().get_ifd(0x8825))

        served = _redacted(data)

        self.assertEqual(len(served), len(data))
        image = Image.open(io.BytesIO(served))
        self.assertEqual(dict(image.getexif().get_ifd(ExifTags.IFD.GPSInfo)), {})
        self.assertEqual(image.getexif().get(ExifTags.Base.Model), "EOS R6")

    def test_olympus_and_panasonic_tiff_magics(self):
        tiff = _gps_tiff()
        for magic in (b"IIRO", b"IIRS", b"IIU\x00"):
            with self.subTest(magic=magic):
                served = _redacted(magic + tiff[4:])

                self.assertFalse(_located(b"II*\x00" + served[4:]))

    def test_the_jpeg_preview_of_a_panasonic_rw2(self):
        preview = _encode("JPEG")
        header = b"IIU\x00" + struct.pack("<I", 8)
        directory = struct.pack("<HHHII", 1, 0x2E, 7, len(preview), 26) + bytes(4)

        served = _redacted(header + directory + preview)

        exif = Image.open(io.BytesIO(served[26:])).getexif()
        self.assertEqual(dict(exif.get_ifd(ExifTags.IFD.GPSInfo)), {})

    def test_the_jpeg_preview_of_a_fujifilm_raf(self):
        preview = _encode("JPEG")
        header = b"FUJIFILMCCD-RAW 0201FF383501".ljust(84, b"\x00")
        header += struct.pack(">II", 100, len(preview))

        served = _redacted(header.ljust(100, b"\x00") + preview)

        exif = Image.open(io.BytesIO(served[100:])).getexif()
        self.assertEqual(dict(exif.get_ifd(ExifTags.IFD.GPSInfo)), {})
        self.assertEqual(exif[ExifTags.Base.Make], "Apple")

    def test_the_gps_block_of_a_canon_cr3(self):
        camera = Image.Exif()
        camera[ExifTags.Base.Make] = "Canon"
        position = Image.Exif()
        position.update(GPS)
        cmt1, cmt4 = camera.tobytes()[6:], position.tobytes()[6:]
        canon = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
        moov = _box(b"uuid", canon + _box(b"CMT1", cmt1) + _box(b"CMT4", cmt4))
        data = _box(b"ftyp", b"crx \x00\x00\x00\x01crx ") + _box(b"moov", moov)
        self.assertEqual(_read_exif(cmt4)[ExifTags.GPS.GPSLatitudeRef], "N")

        served = _redacted(data)

        at = data.index(cmt4)
        self.assertEqual(dict(_read_exif(served[at : at + len(cmt4)])), {})
        at = data.index(cmt1)
        self.assertEqual(_read_exif(served[at : at + len(cmt1)])[271], "Canon")

    def test_a_tiff_photo(self):
        served = _redacted(_gps_tiff())

        self.assertFalse(_located(served))
        self.assertEqual(_read_exif(served)[ExifTags.Base.Make], "Apple")

    def test_a_directory_pointing_outside_the_file(self):
        for data in (b"II*\x00" + struct.pack("<I", 10_000), b"MM\x00*\x00"):
            self.assertEqual(location_patches(io.BytesIO(data)), [])


class ContainerDetailTests(SimpleTestCase):
    def test_a_big_endian_exif_block(self):
        tiff = struct.pack(">2sHI", b"MM", 42, 8)
        tiff += struct.pack(">HHHII", 1, 0x8825, 4, 1, 26) + bytes(4)
        tiff += struct.pack(">HHHII", 1, 2, 5, 3, 44) + bytes(4)
        tiff += struct.pack(">6I", 48, 1, 51, 1, 30, 1)

        served = _redacted(tiff)

        self.assertEqual(served[:26], tiff[:26])
        self.assertEqual(served[26:], bytes(len(tiff) - 26))

    def test_png_xmp(self):
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        info.add_itxt("XML:com.adobe.xmp", XMP.decode())
        buf = io.BytesIO()
        _picture().save(buf, format="PNG", pnginfo=info)

        served = _redacted(buf.getvalue())

        self.assertNotIn(b"51.504", served)
        self.assertTrue(_png_crcs_hold(served))

    def test_a_compressed_png_xmp_is_left_as_is(self):
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        info.add_itxt("XML:com.adobe.xmp", XMP.decode(), zip=True)
        buf = io.BytesIO()
        _picture().save(buf, format="PNG", pnginfo=info)

        self.assertEqual(location_patches(io.BytesIO(buf.getvalue())), [])

    def test_heic_xmp(self):
        buf = io.BytesIO()
        _picture().save(buf, format="HEIF", xmp=XMP, quality=-1)
        self.assertIn(b"51.504", buf.getvalue())

        self.assertNotIn(b"51.504", _redacted(buf.getvalue()))

    def test_video_metadata_boxes(self):
        xmp_uuid = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")
        udta = (
            _box(b"XMP_", XMP)
            + _box(b"loci", bytes(4) + b"home\x00" + bytes(20))
            + _box(b"\xa9xyz", struct.pack(">HH", 18, 0) + b"+48.8584+002.2945/")
        )
        payload = _box(b"udta", udta)
        # A 64-bit size, as a large moov writes it.
        moov = struct.pack(">I4sQ", 1, b"moov", 16 + len(payload)) + payload
        data = (
            _box(b"ftyp", b"isom\x00\x00\x02\x00isom")
            + _box(b"uuid", xmp_uuid + XMP)
            + moov
        )

        served = _redacted(data)

        self.assertEqual(len(served), len(data))
        for value in (b"51.504", b"17.67", b"home", b"48.8584"):
            self.assertNotIn(value, served)
        self.assertEqual(served.count(b"free"), 2)

    def test_a_box_running_to_the_end_of_the_file(self):
        udta = _box(b"udta", _box(b"\xa9xyz", b"\x00\x12\x00\x00+48.8584+002.2945/"))
        data = _box(b"ftyp", b"isom\x00\x00\x02\x00") + b"\x00\x00\x00\x00moov" + udta

        self.assertNotIn(b"48.8584", _redacted(data))

    def test_an_mpf_index_pointing_outside_its_segment(self):
        jpeg = _encode("JPEG")
        segment = b"MPF\x00II*\x00" + struct.pack("<I", 5000)
        app2 = b"\xff\xe2" + struct.pack(">H", 2 + len(segment)) + segment
        data = jpeg[:2] + app2 + jpeg[2:]

        self.assertFalse(
            Image.open(io.BytesIO(_redacted(data))).getexif().get_ifd(0x8825)
        )


class PatchedFileApiTests(SimpleTestCase):
    def test_it_reads_like_the_file_it_wraps(self):
        handle = io.BytesIO(b"abcdef")
        handle.name = "photo.jpg"

        with PatchedFile(handle, [(2, b"XY")]) as patched:
            self.assertEqual(patched.name, "photo.jpg")
            self.assertTrue(patched.seekable())
            self.assertTrue(patched.readable())
            self.assertFalse(patched.closed)
            self.assertEqual(b"".join(patched), b"abXYef")
        self.assertTrue(patched.closed)


class DamagedFileTests(SimpleTestCase):
    """Every file served to someone else is read here: a truncated or
    corrupted one must never fail the response."""

    def fixtures(self):
        from workspace.files.tests.rasters import dng_bytes

        second = _picture((32, 24), (1, 2, 3))
        second.info["exif"] = _exif().tobytes()
        mpo = io.BytesIO()
        _picture().save(
            mpo, format="MPO", save_all=True, append_images=[second], exif=_exif()
        )
        heic = io.BytesIO()
        _picture().save(heic, format="HEIF", xmp=XMP, exif=_exif(), quality=-1)
        xmp_uuid = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")
        video = _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _box(
            b"moov",
            _box(b"udta", _box(b"loci", bytes(24)) + _box(b"XMP_", XMP))
            + _box(b"uuid", xmp_uuid + XMP),
        )
        return {
            "jpeg": _encode("JPEG", xmp=XMP),
            "mpo": mpo.getvalue(),
            "png": _encode("PNG"),
            "webp": _encode("WEBP", xmp=XMP, lossless=True),
            "heic": heic.getvalue(),
            "avif": _encode("AVIF"),
            "dng": dng_bytes(gps=(48, 2)),
            "tiff": _gps_tiff(),
            "video": video,
        }

    def test_truncated_or_corrupted_files_never_fail(self):
        import random

        rng = random.Random(1169)
        for name, data in self.fixtures().items():
            with self.subTest(name=name):
                for cut in range(len(data)):
                    location_patches(io.BytesIO(data[:cut]))
                for _ in range(600):
                    damaged = bytearray(data)
                    for _ in range(4):
                        damaged[rng.randrange(len(damaged))] = rng.randrange(256)
                    location_patches(io.BytesIO(bytes(damaged)))

    def test_a_forged_value_count_allocates_nothing_big(self):
        tiff = struct.pack("<2sHI", b"II", 42, 8)
        tiff += struct.pack("<HHHII", 1, 0x8825, 4, 1, 26) + bytes(4)
        tiff += struct.pack("<HHHII", 1, 2, 5, 100_000, 44) + bytes(4)

        patches = location_patches(io.BytesIO(tiff))

        self.assertEqual(patches, [(26, bytes(14))])


class UnusualLayoutTests(SimpleTestCase):
    def test_jpeg_fill_bytes_and_restart_markers_before_the_exif(self):
        jpeg = _encode("JPEG")
        data = jpeg[:2] + b"\xff\xd0\xff" + jpeg[2:]

        served = _redacted(data)

        exif = Image.open(io.BytesIO(served)).getexif()
        self.assertEqual(dict(exif.get_ifd(ExifTags.IFD.GPSInfo)), {})

    def test_a_webp_exif_chunk_with_the_jpeg_prefix(self):
        exif = b"Exif\x00\x00" + _gps_tiff()
        chunk = b"EXIF" + struct.pack("<I", len(exif)) + exif
        data = b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk

        served = _redacted(data)

        self.assertFalse(_located(served[26:]))

    def test_a_raf_without_a_preview(self):
        header = b"FUJIFILMCCD-RAW 0201FF383501".ljust(84, b"\x00")
        for data in (header, header + struct.pack(">II", 92, 0)):
            self.assertEqual(location_patches(io.BytesIO(data)), [])

    def test_an_apple_location_key_in_a_movie_meta_box(self):
        key = b"com.apple.quicktime.location.ISO6709"
        keys = _box(
            b"keys",
            bytes(4)
            + struct.pack(">I", 2)
            + _box(b"mdta", b"other.key")
            + _box(b"mdta", key),
        )
        value = b"+48.8584+002.2945/"
        ilst = _box(
            b"ilst",
            _box(struct.pack(">I", 1), _box(b"data", struct.pack(">II", 1, 0) + b"x"))
            + _box(
                struct.pack(">I", 2), _box(b"data", struct.pack(">II", 1, 0) + value)
            )
            + _box(struct.pack(">I", 2), _box(b"name", b"short")),
        )
        meta = _box(b"meta", _box(b"hdlr", bytes(24)) + keys + ilst)
        data = _box(b"ftyp", b"qt  \x00\x00\x02\x00") + _box(b"moov", meta)

        served = _redacted(data)

        self.assertNotIn(value, served)
        self.assertIn(b"other.key", served)
