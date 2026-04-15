from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.users.models import UserSetting
from workspace.users.services.settings import (
    _cache_key,
    delete_setting,
    get_all_settings,
    get_module_settings,
    get_setting,
    get_user_timezone,
    set_setting,
)

User = get_user_model()


class GetUserTimezoneTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='alice', password='pass')

    def test_returns_utc_when_no_setting(self):
        tz = get_user_timezone(self.user)
        self.assertEqual(str(tz), 'UTC')

    def test_returns_configured_timezone(self):
        set_setting(self.user, 'core', 'timezone', 'Europe/Paris')
        tz = get_user_timezone(self.user)
        self.assertEqual(str(tz), 'Europe/Paris')

    def test_returns_utc_for_invalid_timezone(self):
        set_setting(self.user, 'core', 'timezone', 'Not/ATimezone')
        tz = get_user_timezone(self.user)
        self.assertEqual(str(tz), 'UTC')

    def test_returns_utc_for_empty_string(self):
        set_setting(self.user, 'core', 'timezone', '')
        tz = get_user_timezone(self.user)
        self.assertEqual(str(tz), 'UTC')


class GetSettingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='alice', password='pass')

    def test_returns_default_when_not_found(self):
        self.assertIsNone(get_setting(self.user, 'core', 'missing'))
        self.assertEqual(get_setting(self.user, 'core', 'missing', default=42), 42)

    def test_returns_value(self):
        set_setting(self.user, 'core', 'theme', 'dark')
        self.assertEqual(get_setting(self.user, 'core', 'theme'), 'dark')

    def test_returns_json_value(self):
        set_setting(self.user, 'core', 'prefs', {'a': 1})
        self.assertEqual(get_setting(self.user, 'core', 'prefs'), {'a': 1})

    def test_returns_null_value(self):
        set_setting(self.user, 'core', 'key', None)
        # Explicitly check it returns None, not the default
        result = get_setting(self.user, 'core', 'key', default='fallback')
        self.assertIsNone(result)


class SetSettingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='alice', password='pass')

    def test_creates_new_setting(self):
        obj = set_setting(self.user, 'core', 'theme', 'dark')
        self.assertEqual(obj.value, 'dark')
        self.assertEqual(UserSetting.objects.count(), 1)

    def test_updates_existing_setting(self):
        set_setting(self.user, 'core', 'theme', 'light')
        set_setting(self.user, 'core', 'theme', 'dark')
        self.assertEqual(UserSetting.objects.count(), 1)
        self.assertEqual(
            UserSetting.objects.get(user=self.user, module='core', key='theme').value,
            'dark',
        )

    def test_returns_model_instance(self):
        obj = set_setting(self.user, 'core', 'theme', 'dark')
        self.assertIsInstance(obj, UserSetting)


class DeleteSettingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='alice', password='pass')

    def test_returns_true_when_deleted(self):
        set_setting(self.user, 'core', 'theme', 'dark')
        self.assertTrue(delete_setting(self.user, 'core', 'theme'))
        self.assertEqual(UserSetting.objects.count(), 0)

    def test_returns_false_when_not_found(self):
        self.assertFalse(delete_setting(self.user, 'core', 'missing'))


class GetModuleSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='alice', password='pass')

    def test_returns_empty_dict_when_none(self):
        self.assertEqual(get_module_settings(self.user, 'core'), {})

    def test_returns_settings_for_module(self):
        set_setting(self.user, 'core', 'theme', 'dark')
        set_setting(self.user, 'core', 'lang', 'fr')
        set_setting(self.user, 'mail', 'sig', 'bye')  # different module
        result = get_module_settings(self.user, 'core')
        self.assertEqual(result, {'theme': 'dark', 'lang': 'fr'})


class GetAllSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='alice', password='pass')

    def test_returns_empty_list_when_none(self):
        self.assertEqual(get_all_settings(self.user), [])

    def test_returns_all_settings(self):
        set_setting(self.user, 'core', 'theme', 'dark')
        set_setting(self.user, 'mail', 'sig', 'bye')
        result = get_all_settings(self.user)
        self.assertEqual(len(result), 2)
        modules = {r['module'] for r in result}
        self.assertEqual(modules, {'core', 'mail'})


class SettingCacheTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass')
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_get_setting_caches_value(self):
        set_setting(self.user, 'core', 'theme', 'dark')
        # First call populates cache
        self.assertEqual(get_setting(self.user, 'core', 'theme'), 'dark')
        # Second call should not hit DB
        with self.assertNumQueries(0):
            self.assertEqual(get_setting(self.user, 'core', 'theme'), 'dark')

    def test_get_setting_caches_default(self):
        # First call — cache miss, DB miss, caches default
        self.assertIsNone(get_setting(self.user, 'core', 'missing'))
        # Second call — served from cache, no DB
        with self.assertNumQueries(0):
            self.assertIsNone(get_setting(self.user, 'core', 'missing'))

    def test_get_setting_caches_none_value(self):
        set_setting(self.user, 'core', 'key', None)
        cache.clear()
        # First call caches the real None value
        result = get_setting(self.user, 'core', 'key', default='fallback')
        self.assertIsNone(result)
        # Second call returns cached None, not the default
        with self.assertNumQueries(0):
            result = get_setting(self.user, 'core', 'key', default='fallback')
            self.assertIsNone(result)

    def test_set_setting_updates_cache(self):
        set_setting(self.user, 'core', 'theme', 'light')
        set_setting(self.user, 'core', 'theme', 'dark')
        # get_setting should return updated value without extra DB hit
        with self.assertNumQueries(0):
            self.assertEqual(get_setting(self.user, 'core', 'theme'), 'dark')

    def test_delete_setting_invalidates_cache(self):
        set_setting(self.user, 'core', 'theme', 'dark')
        # Warm cache
        get_setting(self.user, 'core', 'theme')
        delete_setting(self.user, 'core', 'theme')
        # Cache should be cleared — next get hits DB and returns default
        self.assertIsNone(get_setting(self.user, 'core', 'theme'))
