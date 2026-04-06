from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.users.models import UserSetting

User = get_user_model()


class UserTestMixin:
    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', password='Str0ngP@ss!', email='alice@example.com',
            first_name='Alice', last_name='Smith',
        )
        self.client.force_authenticate(self.user)


# ── UserSearchView ──────────────────────────────────────────────

class UserSearchTests(UserTestMixin, APITestCase):
    URL = '/api/v1/users/search'

    def setUp(self):
        super().setUp()
        self.bob = User.objects.create_user(
            username='bob', password='pass', first_name='Bob', last_name='Jones',
        )
        self.carol = User.objects.create_user(
            username='carol', password='pass', first_name='Carol', last_name='Bobson',
        )

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.get(self.URL, {'q': 'bob'})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_short_query_returns_empty(self):
        resp = self.client.get(self.URL, {'q': 'b'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'], [])

    def test_empty_query_returns_empty(self):
        resp = self.client.get(self.URL, {'q': ''})
        self.assertEqual(resp.data['results'], [])

    def test_search_by_username(self):
        resp = self.client.get(self.URL, {'q': 'bob'})
        usernames = [r['username'] for r in resp.data['results']]
        self.assertIn('bob', usernames)

    def test_search_by_first_name(self):
        resp = self.client.get(self.URL, {'q': 'Carol'})
        usernames = [r['username'] for r in resp.data['results']]
        self.assertIn('carol', usernames)

    def test_search_by_last_name(self):
        resp = self.client.get(self.URL, {'q': 'Jones'})
        usernames = [r['username'] for r in resp.data['results']]
        self.assertIn('bob', usernames)

    def test_excludes_current_user(self):
        resp = self.client.get(self.URL, {'q': 'alice'})
        usernames = [r['username'] for r in resp.data['results']]
        self.assertNotIn('alice', usernames)

    def test_excludes_inactive_users(self):
        self.bob.is_active = False
        self.bob.save()
        resp = self.client.get(self.URL, {'q': 'bob'})
        usernames = [r['username'] for r in resp.data['results']]
        self.assertNotIn('bob', usernames)

    def test_excludes_bots(self):
        from workspace.ai.models import BotProfile
        bot_user = User.objects.create_user(username='botuser', password='pass')
        BotProfile.objects.create(user=bot_user, system_prompt='hi')
        resp = self.client.get(self.URL, {'q': 'botuser'})
        usernames = [r['username'] for r in resp.data['results']]
        self.assertNotIn('botuser', usernames)

    def test_limit_parameter(self):
        resp = self.client.get(self.URL, {'q': 'bob', 'limit': 1})
        self.assertLessEqual(len(resp.data['results']), 1)

    def test_limit_clamped_to_max_50(self):
        resp = self.client.get(self.URL, {'q': 'bob', 'limit': 999})
        self.assertEqual(resp.status_code, 200)

    def test_invalid_limit_defaults_to_10(self):
        resp = self.client.get(self.URL, {'q': 'bob', 'limit': 'abc'})
        self.assertEqual(resp.status_code, 200)

    def test_result_fields(self):
        resp = self.client.get(self.URL, {'q': 'bob'})
        result = next(r for r in resp.data['results'] if r['username'] == 'bob')
        self.assertEqual(set(result.keys()), {'id', 'username', 'first_name', 'last_name'})
        self.assertEqual(result['first_name'], 'Bob')
        self.assertEqual(result['last_name'], 'Jones')


# ── UserMeView ──────────────────────────────────────────────────

class UserMeTests(UserTestMixin, APITestCase):
    URL = '/api/v1/users/me'

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_returns_current_user_data(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['username'], 'alice')
        self.assertEqual(resp.data['email'], 'alice@example.com')
        self.assertEqual(resp.data['first_name'], 'Alice')
        self.assertEqual(resp.data['last_name'], 'Smith')
        self.assertIn('date_joined', resp.data)
        self.assertIn('last_login', resp.data)


# ── PasswordRulesView ──────────────────────────────────────────

class PasswordRulesTests(UserTestMixin, APITestCase):
    URL = '/api/v1/users/password-rules'

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_returns_rules(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('rules', resp.data)
        self.assertIsInstance(resp.data['rules'], list)
        for rule in resp.data['rules']:
            self.assertIn('text', rule)
            self.assertIn('code', rule)


# ── ChangePasswordView ──────────────────────────────────────────

class ChangePasswordTests(UserTestMixin, APITestCase):
    URL = '/api/v1/users/me/password'

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.post(self.URL, {})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_missing_fields(self):
        resp = self.client.post(self.URL, {})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('errors', resp.data)

    def test_wrong_current_password(self):
        resp = self.client.post(self.URL, {
            'current_password': 'wrongpass',
            'new_password': 'N3wStr0ng!Pass',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('incorrect', resp.data['errors'][0].lower())

    def test_weak_new_password(self):
        resp = self.client.post(self.URL, {
            'current_password': 'Str0ngP@ss!',
            'new_password': '123',
        })
        self.assertEqual(resp.status_code, 400)

    def test_successful_password_change(self):
        resp = self.client.post(self.URL, {
            'current_password': 'Str0ngP@ss!',
            'new_password': 'N3wStr0ng!Pass',
        })
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('N3wStr0ng!Pass'))

    def test_session_preserved_after_change(self):
        # Use session-based client instead of force_authenticate
        session_client = self.client_class()
        session_client.login(username='alice', password='Str0ngP@ss!')
        resp = session_client.post(self.URL, {
            'current_password': 'Str0ngP@ss!',
            'new_password': 'N3wStr0ng!Pass',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        # Session should still be valid
        resp2 = session_client.get('/api/v1/users/me')
        self.assertEqual(resp2.status_code, 200)


# ── UserAvatarRetrieveView ──────────────────────────────────────

class UserAvatarRetrieveTests(UserTestMixin, APITestCase):

    def _url(self, user_id):
        return f'/api/v1/users/{user_id}/avatar'

    def test_returns_404_for_nonexistent_user(self):
        resp = self.client.get(self._url(99999))
        self.assertEqual(resp.status_code, 404)

    @patch('workspace.users.views.default_storage')
    def test_returns_404_when_no_avatar(self, mock_storage):
        mock_storage.exists.return_value = False
        resp = self.client.get(self._url(self.user.pk))
        self.assertEqual(resp.status_code, 404)

    @patch('workspace.users.views.default_storage')
    @patch('workspace.users.views.avatar_service')
    def test_returns_avatar_when_exists(self, mock_avatar_svc, mock_storage):
        mock_storage.exists.return_value = True
        mock_storage.open.return_value = BytesIO(b'fake-webp-data')
        mock_avatar_svc.get_avatar_path.return_value = 'avatars/1.webp'
        mock_avatar_svc.get_avatar_etag.return_value = 'abc123'

        resp = self.client.get(self._url(self.user.pk))
        self.assertEqual(resp.status_code, 200)

    @patch('workspace.users.views.default_storage')
    @patch('workspace.users.views.avatar_service')
    def test_returns_304_with_matching_etag(self, mock_avatar_svc, mock_storage):
        mock_storage.exists.return_value = True
        mock_avatar_svc.get_avatar_path.return_value = 'avatars/1.webp'
        mock_avatar_svc.get_avatar_etag.return_value = 'abc123'

        resp = self.client.get(
            self._url(self.user.pk),
            HTTP_IF_NONE_MATCH='"abc123"',
        )
        self.assertEqual(resp.status_code, 304)

    def test_returns_404_for_inactive_user(self):
        inactive = User.objects.create_user(username='gone', password='pass', is_active=False)
        resp = self.client.get(self._url(inactive.pk))
        self.assertEqual(resp.status_code, 404)


# ── UserAvatarUploadView ────────────────────────────────────────

class UserAvatarUploadTests(UserTestMixin, APITestCase):
    URL = '/api/v1/users/me/avatar'

    def _make_image(self, fmt='PNG', size=(100, 100)):
        buf = BytesIO()
        img = Image.new('RGB', size, color='red')
        img.save(buf, format=fmt)
        buf.seek(0)
        buf.name = f'avatar.{fmt.lower()}'
        return buf

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.post(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_no_image_returns_400(self):
        resp = self.client.post(self.URL, {
            'crop_x': 0, 'crop_y': 0, 'crop_w': 50, 'crop_h': 50,
        })
        self.assertEqual(resp.status_code, 400)

    def test_unsupported_content_type_returns_400(self):
        buf = BytesIO(b'not-an-image')
        buf.name = 'avatar.bmp'
        resp = self.client.post(self.URL, {
            'image': buf,
            'crop_x': 0, 'crop_y': 0, 'crop_w': 50, 'crop_h': 50,
        }, format='multipart')
        self.assertEqual(resp.status_code, 400)

    @patch('workspace.users.views.AVATAR_MAX_SIZE', 10)
    def test_oversized_image_returns_400(self):
        buf = self._make_image()
        resp = self.client.post(self.URL, {
            'image': buf,
            'crop_x': 0, 'crop_y': 0, 'crop_w': 50, 'crop_h': 50,
        }, format='multipart')
        self.assertEqual(resp.status_code, 400)

    def test_invalid_crop_coordinates_returns_400(self):
        buf = self._make_image()
        resp = self.client.post(self.URL, {
            'image': buf,
            'crop_x': 'abc', 'crop_y': 0, 'crop_w': 50, 'crop_h': 50,
        }, format='multipart')
        self.assertEqual(resp.status_code, 400)

    def test_zero_crop_dimensions_returns_400(self):
        buf = self._make_image()
        resp = self.client.post(self.URL, {
            'image': buf,
            'crop_x': 0, 'crop_y': 0, 'crop_w': 0, 'crop_h': 50,
        }, format='multipart')
        self.assertEqual(resp.status_code, 400)

    @patch('workspace.users.views.avatar_service')
    def test_successful_upload(self, mock_svc):
        buf = self._make_image()
        resp = self.client.post(self.URL, {
            'image': buf,
            'crop_x': 0, 'crop_y': 0, 'crop_w': 50, 'crop_h': 50,
        }, format='multipart')
        self.assertEqual(resp.status_code, 200)
        mock_svc.process_and_save_avatar.assert_called_once()

    @patch('workspace.users.views.avatar_service')
    def test_delete_avatar(self, mock_svc):
        resp = self.client.delete(self.URL)
        self.assertEqual(resp.status_code, 200)
        mock_svc.delete_avatar.assert_called_once_with(self.user)


# ── UserStatusView ──────────────────────────────────────────────

class UserStatusTests(UserTestMixin, APITestCase):
    URL = '/api/v1/users/me/status'

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_status(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('status', resp.data)

    def test_set_valid_status(self):
        for s in ('auto', 'online', 'away', 'busy', 'invisible'):
            resp = self.client.put(self.URL, {'status': s}, format='json')
            self.assertEqual(resp.status_code, 200, f'Failed for status {s}')
            self.assertEqual(resp.data['status'], s)

    def test_set_invalid_status(self):
        resp = self.client.put(self.URL, {'status': 'bogus'}, format='json')
        self.assertEqual(resp.status_code, 400)


# ── SettingsListView ────────────────────────────────────────────

class SettingsListTests(UserTestMixin, APITestCase):
    URL = '/api/v1/settings'

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_empty(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'], [])

    def test_list_returns_settings(self):
        UserSetting.objects.create(user=self.user, module='core', key='theme', value='dark')
        UserSetting.objects.create(user=self.user, module='core', key='lang', value='fr')
        resp = self.client.get(self.URL)
        self.assertEqual(len(resp.data['results']), 2)

    def test_filter_by_module(self):
        UserSetting.objects.create(user=self.user, module='core', key='theme', value='dark')
        UserSetting.objects.create(user=self.user, module='mail', key='sig', value='hi')
        resp = self.client.get(self.URL, {'module': 'core'})
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['module'], 'core')

    def test_does_not_return_other_users_settings(self):
        other = User.objects.create_user(username='other', password='pass')
        UserSetting.objects.create(user=other, module='core', key='theme', value='dark')
        resp = self.client.get(self.URL)
        self.assertEqual(resp.data['results'], [])


# ── SettingDetailView ───────────────────────────────────────────

class SettingDetailTests(UserTestMixin, APITestCase):

    def _url(self, module, key):
        return f'/api/v1/settings/{module}/{key}'

    def test_get_nonexistent_returns_404(self):
        resp = self.client.get(self._url('core', 'missing'))
        self.assertEqual(resp.status_code, 404)

    def test_get_existing(self):
        UserSetting.objects.create(user=self.user, module='core', key='theme', value='dark')
        resp = self.client.get(self._url('core', 'theme'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['value'], 'dark')

    def test_put_creates_setting(self):
        resp = self.client.put(
            self._url('core', 'theme'), {'value': 'dark'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['value'], 'dark')
        self.assertTrue(UserSetting.objects.filter(
            user=self.user, module='core', key='theme',
        ).exists())

    def test_put_updates_existing(self):
        UserSetting.objects.create(user=self.user, module='core', key='theme', value='light')
        resp = self.client.put(
            self._url('core', 'theme'), {'value': 'dark'}, format='json',
        )
        self.assertEqual(resp.data['value'], 'dark')

    def test_put_json_value(self):
        resp = self.client.put(
            self._url('core', 'prefs'), {'value': {'a': 1, 'b': [2, 3]}}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['value'], {'a': 1, 'b': [2, 3]})

    def test_put_null_value(self):
        resp = self.client.put(
            self._url('core', 'key'), {'value': None}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data['value'])

    def test_delete_existing(self):
        UserSetting.objects.create(user=self.user, module='core', key='theme', value='dark')
        resp = self.client.delete(self._url('core', 'theme'))
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(UserSetting.objects.filter(
            user=self.user, module='core', key='theme',
        ).exists())

    def test_delete_nonexistent_returns_404(self):
        resp = self.client.delete(self._url('core', 'missing'))
        self.assertEqual(resp.status_code, 404)

    def test_cannot_access_other_users_setting(self):
        other = User.objects.create_user(username='other', password='pass')
        UserSetting.objects.create(user=other, module='core', key='theme', value='dark')
        resp = self.client.get(self._url('core', 'theme'))
        self.assertEqual(resp.status_code, 404)


# ── UserGroupsView ──────────────────────────────────────────────

class UserGroupsTests(UserTestMixin, APITestCase):
    URL = '/api/v1/users/groups'

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_returns_empty_when_no_groups(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_returns_user_groups(self):
        group = Group.objects.create(name='Engineering')
        self.user.groups.add(group)
        resp = self.client.get(self.URL)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['name'], 'Engineering')
        self.assertIn('has_folder', resp.data[0])

    def test_has_folder_annotation(self):
        group = Group.objects.create(name='Team')
        self.user.groups.add(group)
        # Create a root folder for the group
        File.objects.create(
            owner=self.user, name='Team Folder', node_type=File.NodeType.FOLDER,
            group=group, parent=None,
        )
        resp = self.client.get(self.URL)
        self.assertTrue(resp.data[0]['has_folder'])

    def test_does_not_return_other_users_groups(self):
        other = User.objects.create_user(username='other', password='pass')
        group = Group.objects.create(name='Secret')
        other.groups.add(group)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.data, [])
