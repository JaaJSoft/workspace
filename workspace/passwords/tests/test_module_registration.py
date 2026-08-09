from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.core.module_registry import registry
from workspace.core.services.module_visibility import (
    filter_visible_commands,
    user_can_see_module,
)

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


class PasswordsCommandTests(TestCase):
    def _passwords_commands(self):
        return [
            cmd
            for cmd in registry.get_active_commands()
            if cmd.module_slug == "passwords"
        ]

    def test_only_the_navigation_command_is_registered(self):
        commands = self._passwords_commands()
        self.assertEqual({cmd.name for cmd in commands}, {"Passwords"})
        self.assertEqual(commands[0].kind, "navigate")
        self.assertEqual(commands[0].url, "/passwords")

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_commands_are_hidden_from_regular_users(self):
        regular = User.objects.create_user(username="nobody", password="pw")
        visible = filter_visible_commands(regular, registry.get_active_commands())
        self.assertNotIn("passwords", {cmd.module_slug for cmd in visible})
