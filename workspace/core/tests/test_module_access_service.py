from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase

from workspace.core.models import ModuleAccessRule
from workspace.core.services.module_access import (
    can_access_module,
    enabled_module_slugs,
    filter_visible,
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

    def test_cache_invalidated_on_group_membership_change(self):
        group = Group.objects.create(name="Blocked")
        ModuleAccessRule.objects.create(
            module_slug="mail", group=group, is_enabled=False
        )
        cache.clear()
        # user not in the denied group yet -> allowed (warms the cache)
        self.assertTrue(can_access_module(self.user, "mail"))
        # joining the denied group must take effect immediately
        self.user.groups.add(group)
        self.assertFalse(can_access_module(self.user, "mail"))
        # leaving it must restore access immediately
        self.user.groups.remove(group)
        self.assertTrue(can_access_module(self.user, "mail"))


class FilterVisibleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="carol", password="x")

    def tearDown(self):
        cache.clear()

    def test_keeps_non_restrictable_items(self):
        items = [{"module_slug": "dashboard"}, {"module_slug": "core"}]
        kept = filter_visible(self.user, items, lambda i: i["module_slug"])
        self.assertEqual(kept, items)

    def test_drops_disabled_restrictable_item(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        cache.clear()
        items = [{"module_slug": "mail"}, {"module_slug": "chat"}]
        kept = filter_visible(self.user, items, lambda i: i["module_slug"])
        slugs = [i["module_slug"] for i in kept]
        self.assertNotIn("mail", slugs)
        self.assertIn("chat", slugs)

    def test_works_with_attribute_getter(self):
        from types import SimpleNamespace

        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        cache.clear()
        items = [SimpleNamespace(slug="mail"), SimpleNamespace(slug="files")]
        kept = filter_visible(self.user, items, lambda i: i.slug)
        slugs = [i.slug for i in kept]
        self.assertNotIn("mail", slugs)
        self.assertIn("files", slugs)
