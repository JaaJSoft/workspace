import base64

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.core.models import ModuleAccessRule

User = get_user_model()


class ApiModuleAccessEnforcementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="demo", password="secret-pass")

    def tearDown(self):
        cache.clear()

    def _basic_auth(self):
        creds = base64.b64encode(b"demo:secret-pass").decode()
        return {"HTTP_AUTHORIZATION": f"Basic {creds}"}

    def test_basic_auth_blocked_on_disabled_module(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        cache.clear()
        resp = self.client.get("/api/v1/mail/accounts", **self._basic_auth())
        self.assertEqual(resp.status_code, 403)

    def test_basic_auth_allowed_when_enabled(self):
        # no rule -> default open
        resp = self.client.get("/api/v1/mail/accounts", **self._basic_auth())
        self.assertNotEqual(resp.status_code, 403)

    def test_session_auth_still_blocked(self):
        self.client.force_login(self.user)
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        cache.clear()
        resp = self.client.get("/api/v1/mail/accounts")
        self.assertEqual(resp.status_code, 403)

    def test_non_restrictable_api_not_blocked(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        cache.clear()
        resp = self.client.get("/api/v1/modules", **self._basic_auth())
        self.assertNotEqual(resp.status_code, 403)

    def test_superuser_basic_auth_not_blocked(self):
        User.objects.create_superuser(username="root", password="rootpass")
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        cache.clear()
        creds = base64.b64encode(b"root:rootpass").decode()
        resp = self.client.get(
            "/api/v1/mail/accounts", HTTP_AUTHORIZATION=f"Basic {creds}"
        )
        self.assertNotEqual(resp.status_code, 403)

    def test_token_auth_blocked_on_disabled_module(self):
        from knox.models import AuthToken

        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        cache.clear()
        _instance, token = AuthToken.objects.create(self.user)
        resp = self.client.get(
            "/api/v1/mail/accounts", HTTP_AUTHORIZATION=f"Token {token}"
        )
        self.assertEqual(resp.status_code, 403)

    def test_token_auth_allowed_when_enabled(self):
        from knox.models import AuthToken

        _instance, token = AuthToken.objects.create(self.user)
        resp = self.client.get(
            "/api/v1/mail/accounts", HTTP_AUTHORIZATION=f"Token {token}"
        )
        self.assertNotEqual(resp.status_code, 403)
