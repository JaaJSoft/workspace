from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import resolve

from workspace.core.module_registry import registry
from workspace.core.services.module_visibility import (
    filter_visible_commands,
    user_can_see_module,
)

User = get_user_model()


class VaultModuleRegistrationTests(TestCase):
    def test_registered_as_an_active_preview_module(self):
        module = registry.get("vault")
        self.assertIsNotNone(module)
        self.assertTrue(module.preview)
        self.assertTrue(module.active)
        self.assertEqual(module.url, "/vault")

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_hidden_from_regular_users(self):
        module = registry.get("vault")
        regular = User.objects.create_user(username="regular", password="pw")
        staff = User.objects.create_user(username="staff", password="pw", is_staff=True)
        self.assertFalse(user_can_see_module(regular, module))
        self.assertTrue(user_can_see_module(staff, module))


class VaultCommandTests(TestCase):
    def _vault_commands(self):
        return [
            cmd for cmd in registry.get_active_commands() if cmd.module_slug == "vault"
        ]

    def test_the_three_navigation_commands_are_registered(self):
        """Held back by the scaffold until a page existed to honour them.

        All three are plain links, which is the whole constraint: a command
        cannot name a vault, so each URL has to reach a page that works out
        which vault it means. `?action=` is how the two verbs travel."""
        commands = self._vault_commands()
        self.assertEqual(
            [(cmd.name, cmd.url) for cmd in commands],
            [
                ("Vault", "/vault"),
                ("Lock vault", "/vault?action=lock"),
                ("New entry", "/vault?action=new"),
            ],
        )

    def test_every_vault_command_is_a_link(self):
        """Not decoration: a command of another kind would be free to carry a
        payload, and the reason these are on /vault rather than on a vault is
        precisely that they cannot."""
        self.assertEqual({cmd.kind for cmd in self._vault_commands()}, {"navigate"})

    def test_every_command_url_resolves_to_the_vault_page(self):
        """A palette entry pointing at a 404 is worse than an absent one."""
        for command in self._vault_commands():
            with self.subTest(command=command.name):
                self.assertEqual(
                    resolve(urlparse(command.url).path).view_name, "vault_ui:index"
                )

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_commands_are_hidden_from_regular_users(self):
        regular = User.objects.create_user(username="nobody", password="pw")
        visible = filter_visible_commands(regular, registry.get_active_commands())
        self.assertNotIn("vault", {cmd.module_slug for cmd in visible})
