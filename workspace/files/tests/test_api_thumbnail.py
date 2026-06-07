from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.services.thumbnails import get_thumbnail_path

User = get_user_model()


class ThumbnailCacheHeadersTests(APITestCase):
    """GET /api/v1/files/<uuid>/thumbnail caching and revalidation."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="thumb-user",
            email="thumb@example.com",
            password="pass",
        )
        self.client.force_authenticate(user=self.user)
        self.file = File.objects.create(
            owner=self.user,
            name="photo.jpg",
            node_type=File.NodeType.FILE,
            mime_type="image/jpeg",
        )
        self.thumb_path = get_thumbnail_path(self.file.uuid)
        if default_storage.exists(self.thumb_path):
            default_storage.delete(self.thumb_path)
        default_storage.save(self.thumb_path, ContentFile(b"fake-webp-bytes"))
        self.url = f"/api/v1/files/{self.file.uuid}/thumbnail"

    def tearDown(self):
        # Windows can hold the file open if the FileResponse handle was not
        # fully consumed - swallow the PermissionError rather than crashing
        # the test (the file is rewritten/cleared on each setUp anyway).
        try:
            if default_storage.exists(self.thumb_path):
                default_storage.delete(self.thumb_path)
        except PermissionError:
            # Best-effort cleanup on Windows; ignore transient file-lock errors.
            return

    def _consume(self, resp):
        # Drain streaming_content so Django closes the file handle. Otherwise
        # Windows refuses to delete the underlying thumbnail in tearDown.
        if hasattr(resp, "streaming_content"):
            b"".join(resp.streaming_content)

    def test_cache_control_is_private_with_swr(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        cc = resp["Cache-Control"]
        self.assertIn("private", cc)
        self.assertNotIn("public", cc)
        self.assertIn("max-age=86400", cc)
        self.assertIn("stale-while-revalidate=604800", cc)
        self._consume(resp)

    def test_etag_present_and_drives_304(self):
        first = self.client.get(self.url)
        self.assertEqual(first.status_code, 200)
        etag = first["ETag"]
        self.assertTrue(etag)
        self._consume(first)

        revalidation = self.client.get(self.url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(revalidation.status_code, 304)
        self.assertEqual(revalidation["ETag"], etag)
