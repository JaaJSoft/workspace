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
