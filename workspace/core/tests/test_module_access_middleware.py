from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.core.models import ModuleAccessRule

User = get_user_model()


class ModuleAccessMiddlewareTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bob", password="x")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_ui_path_blocked_with_403(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        cache.clear()
        resp = self.client.get("/mail")
        self.assertEqual(resp.status_code, 403)

    def test_api_path_blocked_with_json_403(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        cache.clear()
        resp = self.client.get("/api/v1/mail/accounts")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp["Content-Type"], "application/json")

    def test_enabled_module_not_blocked(self):
        # no rule -> default open; mail must not 403 at the middleware layer
        resp = self.client.get("/api/v1/mail/accounts")
        self.assertNotEqual(resp.status_code, 403)

    def test_non_restrictable_module_never_blocked(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        cache.clear()
        resp = self.client.get("/api/v1/modules")
        self.assertNotEqual(resp.status_code, 403)
