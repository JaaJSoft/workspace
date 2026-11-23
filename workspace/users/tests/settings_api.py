"""A signed-in client on the settings API, for a module testing the settings
it declares (its ``user_settings.py``)."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.users.services.settings import get_module_settings

User = get_user_model()


class SettingsApiTestCase(TestCase):
    module = None

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="settings", password="pass")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def put(self, key, value, module=None):
        return self.client.put(
            f"/api/v1/settings/{module or self.module}/{key}",
            data={"value": value},
            content_type="application/json",
        )

    def stored(self, module=None):
        return get_module_settings(self.user, module or self.module)

    def assertAccepts(self, key, values, module=None):
        for value in values:
            with self.subTest(key=key, value=value):
                response = self.put(key, value, module)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(self.stored(module)[key], value)

    def assertRefuses(self, key, values, module=None):
        before = self.stored(module).get(key)
        for value in values:
            with self.subTest(key=key, value=value):
                self.assertEqual(self.put(key, value, module).status_code, 400)
                self.assertEqual(self.stored(module).get(key), before)
