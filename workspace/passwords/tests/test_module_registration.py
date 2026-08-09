from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.core.module_registry import registry
from workspace.core.services.module_visibility import user_can_see_module

User = get_user_model()


class PasswordsModuleRegistrationTests(TestCase):
    def test_registered_as_an_active_preview_module(self):
        module = registry.get("passwords")
        self.assertIsNotNone(module)
        self.assertTrue(module.preview)
        self.assertTrue(module.active)
        self.assertEqual(module.url, "/passwords")

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_hidden_from_regular_users(self):
        module = registry.get("passwords")
        regular = User.objects.create_user(username="regular", password="pw")
        staff = User.objects.create_user(username="staff", password="pw", is_staff=True)
        self.assertFalse(user_can_see_module(regular, module))
        self.assertTrue(user_can_see_module(staff, module))
