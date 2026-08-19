from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.core.module_registry import registry
from workspace.core.services.module_visibility import (
    filter_visible_commands,
    user_can_see_module,
)

User = get_user_model()


class ImportsModuleRegistrationTests(TestCase):
    def test_registered_as_an_active_preview_module_off_the_dashboard(self):
        module = registry.get("imports")
        self.assertIsNotNone(module)
        self.assertTrue(module.active)
        self.assertTrue(module.preview)
        self.assertFalse(module.show_on_dashboard)
        self.assertEqual(module.url, "/imports")

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_hidden_from_regular_users_while_in_preview(self):
        module = registry.get("imports")
        regular = User.objects.create_user(username="regular", password="pw")
        staff = User.objects.create_user(username="staff", password="pw", is_staff=True)
        self.assertFalse(user_can_see_module(regular, module))
        self.assertTrue(user_can_see_module(staff, module))


class ImportsCommandTests(TestCase):
    def _commands(self):
        return [
            cmd
            for cmd in registry.get_active_commands()
            if cmd.module_slug == "imports"
        ]

    def test_navigation_commands_are_registered(self):
        commands = self._commands()
        self.assertEqual(
            {(cmd.name, cmd.url) for cmd in commands},
            {
                ("Import from another cloud", "/imports?new=1"),
                ("My imports", "/imports"),
            },
        )
        self.assertTrue(all(cmd.kind == "navigate" for cmd in commands))

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_commands_follow_module_visibility(self):
        regular = User.objects.create_user(username="nobody", password="pw")
        visible = filter_visible_commands(regular, registry.get_active_commands())
        self.assertNotIn("imports", {cmd.module_slug for cmd in visible})
