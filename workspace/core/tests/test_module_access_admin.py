from django.contrib.admin.sites import site
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.core.admin import ModuleAccessRuleForm
from workspace.core.models import ModuleAccessRule


class ModuleAccessAdminTests(TestCase):
    def test_model_registered(self):
        self.assertIn(ModuleAccessRule, site._registry)

    def test_form_slug_choices_are_restrictable_modules(self):
        form = ModuleAccessRuleForm()
        choice_values = {value for value, _ in form.fields["module_slug"].choices}
        self.assertIn("mail", choice_values)
        self.assertNotIn("core", choice_values)

    def test_form_rejects_unknown_slug(self):
        form = ModuleAccessRuleForm(data={"module_slug": "nope", "is_enabled": True})
        self.assertFalse(form.is_valid())
        self.assertIn("module_slug", form.errors)

    def test_form_accepts_valid_global_rule(self):
        form = ModuleAccessRuleForm(data={"module_slug": "mail", "is_enabled": False})
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_rejects_duplicate_global_rule(self):
        ModuleAccessRule.objects.create(module_slug="mail", is_enabled=True)
        form = ModuleAccessRuleForm(data={"module_slug": "mail", "is_enabled": False})
        self.assertFalse(form.is_valid())

    def test_form_rejects_duplicate_group_rule(self):
        group = Group.objects.create(name="Sales")
        ModuleAccessRule.objects.create(
            module_slug="mail", group=group, is_enabled=True
        )
        form = ModuleAccessRuleForm(
            data={"module_slug": "mail", "group": str(group.pk), "is_enabled": False}
        )
        self.assertFalse(form.is_valid())
