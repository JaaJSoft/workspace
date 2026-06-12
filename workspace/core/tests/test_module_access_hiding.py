from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from workspace.core.context_processors import workspace_modules
from workspace.core.models import ModuleAccessRule

User = get_user_model()


class ModuleHidingTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="bob", password="x")

    def tearDown(self):
        cache.clear()

    def _context(self):
        request = self.factory.get("/")
        request.user = self.user
        return workspace_modules(request)

    def test_enabled_module_present(self):
        slugs = {m["slug"] for m in self._context()["workspace_active_modules"]}
        self.assertIn("mail", slugs)

    def test_disabled_module_hidden_from_nav(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        cache.clear()
        ctx = self._context()
        slugs = {m["slug"] for m in ctx["workspace_active_modules"]}
        self.assertNotIn("mail", slugs)
        cmd_modules = {c["module_slug"] for c in ctx["workspace_commands"]}
        self.assertNotIn("mail", cmd_modules)
