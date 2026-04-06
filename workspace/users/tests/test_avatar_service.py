from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from PIL import Image

from workspace.users import avatar_service
from workspace.users.settings_service import get_setting, set_setting

User = get_user_model()


class GetAvatarPathTests(TestCase):
    def test_returns_expected_path(self):
        self.assertEqual(avatar_service.get_avatar_path(42), 'avatars/42.webp')


class HasAvatarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass')

    def test_false_by_default(self):
        self.assertFalse(avatar_service.has_avatar(self.user))

    def test_true_when_setting_set(self):
        set_setting(self.user, 'profile', 'has_avatar', True)
        self.assertTrue(avatar_service.has_avatar(self.user))


class ProcessAndSaveAvatarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass')

    def _make_image(self, size=(200, 200)):
        buf = BytesIO()
        img = Image.new('RGB', size, color='blue')
        img.save(buf, format='PNG')
        buf.seek(0)
        return buf

    @patch('workspace.users.avatar_service.save_image')
    @patch('workspace.users.avatar_service.process_image_to_webp')
    def test_calls_process_and_save(self, mock_process, mock_save):
        mock_process.return_value = b'webp-bytes'
        image_file = self._make_image()

        avatar_service.process_and_save_avatar(
            self.user, image_file, 0, 0, 100, 100,
        )

        mock_process.assert_called_once_with(image_file, 0, 0, 100, 100)
        mock_save.assert_called_once_with('avatars/{}.webp'.format(self.user.id), b'webp-bytes')

    @patch('workspace.users.avatar_service.save_image')
    @patch('workspace.users.avatar_service.process_image_to_webp')
    def test_sets_has_avatar_setting(self, mock_process, mock_save):
        mock_process.return_value = b'webp-bytes'
        avatar_service.process_and_save_avatar(
            self.user, self._make_image(), 0, 0, 100, 100,
        )
        self.assertTrue(get_setting(self.user, 'profile', 'has_avatar'))


class DeleteAvatarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass')

    @patch('workspace.users.avatar_service.delete_image')
    def test_deletes_file_and_clears_setting(self, mock_delete):
        set_setting(self.user, 'profile', 'has_avatar', True)
        avatar_service.delete_avatar(self.user)
        mock_delete.assert_called_once_with(f'avatars/{self.user.id}.webp')
        self.assertFalse(avatar_service.has_avatar(self.user))


class GetAvatarEtagTests(TestCase):
    @patch('workspace.users.avatar_service.get_image_etag')
    def test_delegates_to_image_service(self, mock_etag):
        mock_etag.return_value = 'abc123'
        result = avatar_service.get_avatar_etag(7)
        mock_etag.assert_called_once_with('avatars/7.webp')
        self.assertEqual(result, 'abc123')

    @patch('workspace.users.avatar_service.get_image_etag')
    def test_returns_none_when_no_file(self, mock_etag):
        mock_etag.return_value = None
        self.assertIsNone(avatar_service.get_avatar_etag(7))
