from io import BytesIO

from django.core.files.storage import default_storage
from django.test import TestCase
from PIL import Image

from workspace.common.services.image import (
    delete_image,
    get_image_etag,
    process_image_to_webp,
    save_image,
)


class ProcessImageToWebpTests(TestCase):

    def _make_image(self, size=(200, 200), fmt='PNG'):
        buf = BytesIO()
        img = Image.new('RGB', size, color='blue')
        img.save(buf, format=fmt)
        buf.seek(0)
        return buf

    def test_returns_webp_bytes(self):
        buf = self._make_image()
        result = process_image_to_webp(buf, 0, 0, 100, 100)
        self.assertIsInstance(result, bytes)
        # Verify it's valid WebP
        img = Image.open(BytesIO(result))
        self.assertEqual(img.format, 'WEBP')

    def test_output_is_square_256(self):
        buf = self._make_image()
        result = process_image_to_webp(buf, 0, 0, 100, 100)
        img = Image.open(BytesIO(result))
        self.assertEqual(img.size, (256, 256))

    def test_custom_size(self):
        buf = self._make_image()
        result = process_image_to_webp(buf, 0, 0, 100, 100, size=128)
        img = Image.open(BytesIO(result))
        self.assertEqual(img.size, (128, 128))

    def test_crop_applied(self):
        buf = self._make_image(size=(400, 400))
        result = process_image_to_webp(buf, 50, 50, 100, 100)
        self.assertIsInstance(result, bytes)
        img = Image.open(BytesIO(result))
        self.assertEqual(img.size, (256, 256))

    def test_handles_rgba_image(self):
        buf = BytesIO()
        img = Image.new('RGBA', (200, 200), color=(255, 0, 0, 128))
        img.save(buf, format='PNG')
        buf.seek(0)
        result = process_image_to_webp(buf, 0, 0, 100, 100)
        out = Image.open(BytesIO(result))
        self.assertEqual(out.mode, 'RGB')


class SaveImageTests(TestCase):

    def test_saves_and_reads_back(self):
        path = 'test_save_img.webp'
        try:
            save_image(path, b'fake-webp-data')
            self.assertTrue(default_storage.exists(path))
            with default_storage.open(path, 'rb') as f:
                self.assertEqual(f.read(), b'fake-webp-data')
        finally:
            if default_storage.exists(path):
                default_storage.delete(path)

    def test_replaces_existing_file(self):
        path = 'test_replace_img.webp'
        try:
            save_image(path, b'first')
            save_image(path, b'second')
            with default_storage.open(path, 'rb') as f:
                self.assertEqual(f.read(), b'second')
        finally:
            if default_storage.exists(path):
                default_storage.delete(path)


class DeleteImageTests(TestCase):

    def test_deletes_existing_file(self):
        path = 'test_del_img.webp'
        save_image(path, b'data')
        delete_image(path)
        self.assertFalse(default_storage.exists(path))

    def test_no_error_for_nonexistent_file(self):
        delete_image('nonexistent.webp')


class GetImageEtagTests(TestCase):

    def test_returns_etag_for_existing_file(self):
        path = 'test_etag_img.webp'
        try:
            save_image(path, b'data')
            etag = get_image_etag(path)
            self.assertIsNotNone(etag)
            self.assertIsInstance(etag, str)
            self.assertGreater(len(etag), 10)
        finally:
            if default_storage.exists(path):
                default_storage.delete(path)

    def test_returns_none_for_nonexistent_file(self):
        self.assertIsNone(get_image_etag('nonexistent.webp'))

    def test_consistent_for_same_file(self):
        path = 'test_etag_consistent.webp'
        try:
            save_image(path, b'data')
            e1 = get_image_etag(path)
            e2 = get_image_etag(path)
            self.assertEqual(e1, e2)
        finally:
            if default_storage.exists(path):
                default_storage.delete(path)
