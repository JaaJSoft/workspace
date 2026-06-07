from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from PIL import Image

from workspace.users.services import avatar as avatar_service
from workspace.users.services.settings import get_setting, set_setting

User = get_user_model()


class GetAvatarPathTests(TestCase):
    def test_returns_expected_path(self):
        self.assertEqual(avatar_service.get_avatar_path(42), "avatars/42.webp")


class HasAvatarTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="alice", password="pass")

    def test_false_by_default(self):
        self.assertFalse(avatar_service.has_avatar(self.user))

    def test_true_when_setting_set(self):
        set_setting(self.user, "profile", "has_avatar", True)
        self.assertTrue(avatar_service.has_avatar(self.user))


class ProcessAndSaveAvatarTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="alice", password="pass")

    def _make_image(self, size=(200, 200)):
        buf = BytesIO()
        img = Image.new("RGB", size, color="blue")
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    @patch("workspace.users.services.avatar.save_image")
    @patch("workspace.users.services.avatar.process_image_to_webp")
    def test_calls_process_and_save(self, mock_process, mock_save):
        mock_process.return_value = b"webp-bytes"
        image_file = self._make_image()

        avatar_service.process_and_save_avatar(
            self.user,
            image_file,
            0,
            0,
            100,
            100,
        )

        mock_process.assert_called_once_with(image_file, 0, 0, 100, 100)
        mock_save.assert_called_once_with(f"avatars/{self.user.id}.webp", b"webp-bytes")

    @patch("workspace.users.services.avatar.save_image")
    @patch("workspace.users.services.avatar.process_image_to_webp")
    def test_sets_has_avatar_setting(self, mock_process, mock_save):
        mock_process.return_value = b"webp-bytes"
        avatar_service.process_and_save_avatar(
            self.user,
            self._make_image(),
            0,
            0,
            100,
            100,
        )
        self.assertTrue(get_setting(self.user, "profile", "has_avatar"))


class DeleteAvatarTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="alice", password="pass")

    @patch("workspace.users.services.avatar.delete_image")
    def test_deletes_file_and_clears_setting(self, mock_delete):
        set_setting(self.user, "profile", "has_avatar", True)
        avatar_service.delete_avatar(self.user)
        mock_delete.assert_called_once_with(f"avatars/{self.user.id}.webp")
        self.assertFalse(avatar_service.has_avatar(self.user))


class GetAvatarEtagTests(TestCase):
    @patch("workspace.users.services.avatar.get_image_etag")
    def test_delegates_to_image_service(self, mock_etag):
        mock_etag.return_value = "abc123"
        result = avatar_service.get_avatar_etag(7)
        mock_etag.assert_called_once_with("avatars/7.webp")
        self.assertEqual(result, "abc123")

    @patch("workspace.users.services.avatar.get_image_etag")
    def test_returns_none_when_no_file(self, mock_etag):
        mock_etag.return_value = None
        self.assertIsNone(avatar_service.get_avatar_etag(7))


class UserAvatarRetrieveCacheHeadersTests(TestCase):
    """GET /api/v1/users/<id>/avatar must opt into stale-while-revalidate."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="alice", password="pass")
        # Drop a fake webp into storage so the view hits the success path.
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        self._path = avatar_service.get_avatar_path(self.user.id)
        if default_storage.exists(self._path):
            default_storage.delete(self._path)
        default_storage.save(self._path, ContentFile(b"fake-webp-bytes"))

    def tearDown(self):
        from django.core.files.storage import default_storage

        try:
            if default_storage.exists(self._path):
                default_storage.delete(self._path)
        except PermissionError:
            # Windows: FileResponse may still hold the handle. Tolerate it.
            pass
        cache.clear()

    def test_cache_control_uses_swr(self):
        resp = self.client.get(f"/api/v1/users/{self.user.id}/avatar")
        self.assertEqual(resp.status_code, 200)
        cc = resp["Cache-Control"]
        self.assertIn("private", cc)
        self.assertIn("max-age=300", cc)
        self.assertIn("stale-while-revalidate=86400", cc)
        self.assertIn("ETag", resp)
        # Drain the body so the file handle is closed.
        if hasattr(resp, "streaming_content"):
            b"".join(resp.streaming_content)
