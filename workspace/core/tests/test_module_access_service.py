from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase

from workspace.core.models import ModuleAccessRule
from workspace.core.services.module_access import (
    can_access_module,
    enabled_module_slugs,
)

User = get_user_model()


class ModuleAccessServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bob", password="x")

    def tearDown(self):
        cache.clear()

    def test_default_open_when_no_rules(self):
        self.assertTrue(can_access_module(self.user, "mail"))
        self.assertIn("mail", enabled_module_slugs(self.user))

    def test_non_restrictable_always_allowed(self):
        self.assertTrue(can_access_module(self.user, "dashboard"))
        self.assertTrue(can_access_module(self.user, "unknown_slug"))

    def test_global_deny(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        cache.clear()
        self.assertFalse(can_access_module(self.user, "mail"))
        self.assertNotIn("mail", enabled_module_slugs(self.user))

    def test_global_allow_rule(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=True)
        cache.clear()
        self.assertTrue(can_access_module(self.user, "mail"))

    def test_group_deny_denylist(self):
        group = Group.objects.create(name="Interns")
        self.user.groups.add(group)
        ModuleAccessRule.objects.create(
            module_slug="mail", group=group, is_enabled=False
        )
        cache.clear()
        self.assertFalse(can_access_module(self.user, "mail"))
        other = User.objects.create_user(username="alice", password="x")
        self.assertTrue(can_access_module(other, "mail"))

    def test_group_grant_allowlist(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        group = Group.objects.create(name="Staff")
        self.user.groups.add(group)
        ModuleAccessRule.objects.create(
            module_slug="mail", group=group, is_enabled=True
        )
        cache.clear()
        self.assertTrue(can_access_module(self.user, "mail"))

    def test_multi_group_grant_wins(self):
        deny_group = Group.objects.create(name="Deny")
        grant_group = Group.objects.create(name="Grant")
        self.user.groups.add(deny_group, grant_group)
        ModuleAccessRule.objects.create(
            module_slug="mail", group=deny_group, is_enabled=False
        )
        ModuleAccessRule.objects.create(
            module_slug="mail", group=grant_group, is_enabled=True
        )
        cache.clear()
        self.assertTrue(can_access_module(self.user, "mail"))

    def test_superuser_bypasses_all_rules(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        admin = User.objects.create_superuser(username="root", password="x")
        cache.clear()
        self.assertTrue(can_access_module(admin, "mail"))

    def test_cache_invalidated_on_rule_write(self):
        self.assertTrue(can_access_module(self.user, "mail"))
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        self.assertFalse(can_access_module(self.user, "mail"))
