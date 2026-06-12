from django.test import TestCase

from workspace.core.services.module_access import restrictable_module_slugs


class RestrictableModulesTests(TestCase):
    def test_flagged_modules_are_restrictable(self):
        slugs = restrictable_module_slugs()
        for slug in ("notes", "chat", "calendar", "mail"):
            self.assertIn(slug, slugs)

    def test_infra_modules_are_not_restrictable(self):
        slugs = restrictable_module_slugs()
        for slug in ("ai", "files", "core", "dashboard", "users", "notifications", "common"):
            self.assertNotIn(slug, slugs)
