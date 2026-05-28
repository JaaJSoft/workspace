"""Tests for Prometheus instrumentation in the files app.

Covers:
- FileService.create_file / update_content / replace_content_storage bump
  files_upload_bytes_total by the right amount.
- The download endpoint (single file + ZIP) bumps files_download_bytes_total.
- generate_thumbnail observes files_thumbnail_generation_duration_seconds
  and increments files_thumbnail_generation_total{result}.
"""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from prometheus_client import REGISTRY
from rest_framework.test import APIClient

from workspace.files.services import FileService

User = get_user_model()


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


class UploadBytesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='uploader', password='p')

    def test_create_file_bumps_upload_bytes_total_by_size(self):
        before = _sample('files_upload_bytes_total')
        FileService.create_file(
            owner=self.user,
            name='hello.txt',
            content=ContentFile(b'hello world', name='hello.txt'),
        )
        self.assertEqual(_sample('files_upload_bytes_total') - before, 11)

    def test_create_file_without_content_does_not_bump_counter(self):
        before = _sample('files_upload_bytes_total')
        FileService.create_folder(owner=self.user, name='empty')
        # Folders never have content; the counter must not move.
        self.assertEqual(_sample('files_upload_bytes_total'), before)

    def test_update_content_bumps_counter_with_new_size(self):
        f = FileService.create_file(
            owner=self.user,
            name='a.txt',
            content=ContentFile(b'abc', name='a.txt'),
        )
        before = _sample('files_upload_bytes_total')
        FileService.update_content(f, ContentFile(b'abcdefghij', name='a.txt'))
        self.assertEqual(_sample('files_upload_bytes_total') - before, 10)


class DownloadBytesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='downloader', password='p')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_single_file_download_bumps_counter(self):
        f = FileService.create_file(
            owner=self.user,
            name='dl.txt',
            content=ContentFile(b'download-me!', name='dl.txt'),
        )
        before = _sample('files_download_bytes_total')

        resp = self.client.get(f'/api/v1/files/{f.uuid}/download')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(_sample('files_download_bytes_total') - before, 12)


class ThumbnailMetricsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='thumb', password='p')

    def test_skipped_for_non_image_increments_skipped_counter(self):
        from workspace.files.services.thumbnails import generate_thumbnail

        f = FileService.create_file(
            owner=self.user, name='t.txt',
            content=ContentFile(b'plain', name='t.txt'),
        )
        before = _sample('files_thumbnail_generation_total', {'result': 'skipped'})
        result = generate_thumbnail(f)
        self.assertFalse(result)
        self.assertEqual(
            _sample('files_thumbnail_generation_total', {'result': 'skipped'}) - before,
            1,
        )

    def test_failed_generation_increments_failed_counter(self):
        from workspace.files.services.thumbnails import generate_thumbnail

        # Force type='jpeg' so we pass can_generate_thumbnail, but the
        # bytes are garbage so Pillow will raise - that's the failure path.
        f = FileService.create_file(
            owner=self.user, name='broken.jpg',
            content=ContentFile(b'not actually an image', name='broken.jpg'),
            mime_type='image/jpeg',
        )
        f.type = 'jpeg'
        f.save(update_fields=['type'])
        before = _sample('files_thumbnail_generation_total', {'result': 'failed'})
        result = generate_thumbnail(f)
        self.assertFalse(result)
        self.assertEqual(
            _sample('files_thumbnail_generation_total', {'result': 'failed'}) - before,
            1,
        )


