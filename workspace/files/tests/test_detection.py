from django.test import TestCase


class DetectFromBytesTest(TestCase):
    def test_detects_python(self):
        from workspace.files.services.detection import detect_from_bytes

        result = detect_from_bytes(
            b'import os\nimport sys\n\ndef main():\n    print("hello world")\n'
        )
        self.assertEqual(result.label, "python")
        self.assertEqual(result.group, "code")
        self.assertGreater(result.score, 0.0)
        self.assertGreater(len(result.mime_type), 0)

    def test_detects_json(self):
        from workspace.files.services.detection import detect_from_bytes

        result = detect_from_bytes(b'{"key": "value", "number": 42}')
        # Magika classifies single JSON objects as jsonl
        self.assertIn(result.label, ("json", "jsonl"))

    def test_detects_html(self):
        from workspace.files.services.detection import detect_from_bytes

        result = detect_from_bytes(
            b"<!DOCTYPE html>\n<html><head><title>Test</title></head>"
            b"<body></body></html>"
        )
        self.assertEqual(result.label, "html")

    def test_empty_bytes(self):
        from workspace.files.services.detection import detect_from_bytes

        result = detect_from_bytes(b"")
        self.assertEqual(result.label, "empty")

    def test_unknown_binary(self):
        from workspace.files.services.detection import detect_from_bytes

        result = detect_from_bytes(bytes(range(256)) * 4)
        self.assertIsNotNone(result.label)


class DetectFromStreamTest(TestCase):
    def test_detects_from_stream(self):
        from io import BytesIO

        from workspace.files.services.detection import detect_from_stream

        stream = BytesIO(b'{"key": "value"}')
        result = detect_from_stream(stream)
        self.assertIn(result.label, ("json", "jsonl"))

    def test_stream_position_irrelevant(self):
        from io import BytesIO

        from workspace.files.services.detection import detect_from_stream

        stream = BytesIO(b'import os\nprint("hi")\n')
        stream.read()  # advance to end
        result = detect_from_stream(stream)
        self.assertIsNotNone(result.label)

    def test_large_upload_is_not_read_into_memory(self):
        """Django spools an upload over 2.5 MB to a temp file whose wrapper
        Magika rejects; detection must still read only the bytes it samples."""
        import tracemalloc

        from django.core.files.uploadedfile import TemporaryUploadedFile

        from workspace.files.services.detection import (
            detect_from_bytes,
            detect_from_stream,
        )

        upload = TemporaryUploadedFile("big.py", "text/x-python", 0, "utf-8")
        self.addCleanup(upload.close)
        upload.write(b'import os\nprint("hi")\n' * 1_000_000)  # 22 MB
        upload.seek(5)
        # Loads the process-wide model now, so the peak below is the detection.
        detect_from_bytes(b"")

        tracemalloc.start()
        self.addCleanup(tracemalloc.stop)
        result = detect_from_stream(upload)
        peak = tracemalloc.get_traced_memory()[1]

        self.assertEqual(result.label, "python")
        self.assertLess(peak, 2 * 1024 * 1024)
        self.assertEqual(upload.tell(), 5)


class LabelFromNameTest(TestCase):
    def test_known_extension(self):
        from workspace.files.services.detection import label_from_name

        self.assertEqual(label_from_name("notes.md"), "markdown")

    def test_unknown_extension(self):
        from workspace.files.services.detection import label_from_name

        self.assertEqual(label_from_name("data.xyz123"), "unknown")

    def test_empty_filename(self):
        from workspace.files.services.detection import label_from_name

        self.assertEqual(label_from_name(""), "unknown")

    def test_case_insensitive(self):
        from workspace.files.services.detection import label_from_name

        self.assertEqual(label_from_name("PHOTO.JPG"), "jpeg")


class DetectFromNameTest(TestCase):
    def test_known_extension(self):
        from workspace.files.services.detection import detect_from_name

        result = detect_from_name("script.py")
        self.assertEqual(result.label, "python")

    def test_unknown_extension(self):
        from workspace.files.services.detection import detect_from_name

        result = detect_from_name("data.xyz123")
        self.assertEqual(result.label, "unknown")

    def test_no_extension(self):
        from workspace.files.services.detection import detect_from_name

        result = detect_from_name("README")
        self.assertIsNotNone(result.label)

    def test_empty_filename(self):
        from workspace.files.services.detection import detect_from_name

        result = detect_from_name("")
        self.assertEqual(result.label, "unknown")
        self.assertEqual(result.score, 0.0)

    def test_common_extensions(self):
        from workspace.files.services.detection import detect_from_name

        cases = {
            "photo.jpg": "jpeg",
            "photo.jpeg": "jpeg",
            "image.png": "png",
            "doc.pdf": "pdf",
            "video.mp4": "mp4",
            "song.mp3": "mp3",
            "archive.zip": "zip",
            "styles.css": "css",
            "page.html": "html",
            "data.json": "json",
            "notes.md": "markdown",
            "app.js": "javascript",
            "main.go": "go",
            "lib.rs": "rust",
            "doc.docx": "docx",
            "sheet.xlsx": "xlsx",
        }
        for filename, expected_label in cases.items():
            result = detect_from_name(filename)
            self.assertEqual(
                result.label,
                expected_label,
                msg=f"{filename} -> expected {expected_label}, got {result.label}",
            )


class IsoMediaImageTest(TestCase):
    """HEIF and AVIF share the MP4 container; the ftyp brand tells them apart."""

    def test_heic_is_a_heif_image_not_an_mp4(self):
        from workspace.files.services.detection import detect_from_bytes
        from workspace.files.tests.rasters import heic_bytes

        result = detect_from_bytes(heic_bytes())

        self.assertEqual(
            (result.label, result.group, result.mime_type),
            ("heif", "image", "image/heic"),
        )

    def test_avif_is_an_image(self):
        from workspace.files.services.detection import detect_from_bytes
        from workspace.files.tests.rasters import avif_bytes

        result = detect_from_bytes(avif_bytes())

        self.assertEqual(
            (result.label, result.group, result.mime_type),
            ("avif", "image", "image/avif"),
        )

    def test_stream_detection_reads_the_brand_and_rewinds(self):
        from io import BytesIO

        from workspace.files.services.detection import detect_from_stream
        from workspace.files.tests.rasters import heic_bytes

        stream = BytesIO(heic_bytes())

        self.assertEqual(detect_from_stream(stream).label, "heif")
        self.assertEqual(stream.tell(), 0)

    def test_a_generic_heif_brand_defers_to_the_compatible_ones(self):
        from workspace.files.services.detection import _image_label_from_ftyp
        from workspace.files.tests.rasters import ftyp_box

        self.assertEqual(
            _image_label_from_ftyp(ftyp_box(b"mif1", b"mif1", b"heic")), "heif"
        )
        self.assertEqual(
            _image_label_from_ftyp(ftyp_box(b"mif1", b"mif1", b"miaf", b"avif")),
            "avif",
        )

    def test_hevc_and_quicktime_videos_stay_videos(self):
        from workspace.files.services.detection import detect_from_bytes
        from workspace.files.tests.videos import clip_bytes

        self.assertEqual(detect_from_bytes(clip_bytes("clip_hevc.mp4")).label, "mp4")
        self.assertEqual(detect_from_bytes(clip_bytes("clip_iphone.mov")).label, "qt")

    def test_avif_extension_is_an_image(self):
        from workspace.files.services.detection import detect_from_name

        self.assertEqual(detect_from_name("photo.avif").group, "image")
        self.assertEqual(detect_from_name("IMG_0001.HEIC").label, "heif")
