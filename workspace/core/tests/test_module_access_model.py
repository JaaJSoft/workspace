from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from workspace.core.models import ModuleAccessRule

User = get_user_model()


class ModuleAccessRuleModelTests(TestCase):
    def test_create_global_rule(self):
        rule = ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        self.assertIsNone(rule.group)
        self.assertFalse(rule.is_enabled)
        self.assertIsNotNone(rule.uuid)

    def test_create_group_rule(self):
        group = Group.objects.create(name="Marketing")
        rule = ModuleAccessRule.objects.create(
            module_slug="mail", group=group, is_enabled=True
        )
        self.assertEqual(rule.group, group)

    def test_unique_module_group_pair(self):
        group = Group.objects.create(name="Marketing")
        ModuleAccessRule.objects.create(
            module_slug="mail", group=group, is_enabled=True
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ModuleAccessRule.objects.create(
                    module_slug="mail", group=group, is_enabled=False
                )

    def test_single_global_rule_per_module(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=False)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ModuleAccessRule.objects.create(module_slug="mail", is_enabled=True)

    def test_clean_rejects_unknown_slug(self):
        rule = ModuleAccessRule(module_slug="not_a_module", is_enabled=False)
        with self.assertRaises(ValidationError):
            rule.full_clean()

    def test_clean_accepts_restrictable_slug(self):
        rule = ModuleAccessRule(module_slug="mail", is_enabled=False)
        rule.full_clean()  # must not raise
