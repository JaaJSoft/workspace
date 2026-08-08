"""The data migration that turns daisyUI tag colors into CSS colors.

Run against the migration's own functions rather than a historical
schema: the column is a CharField either way, so the mapping is the only
thing worth pinning — and it is what silently loses a user's colors if it
is wrong.
"""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.files.models import Tag

# Migration module names are not valid identifiers, so import by string.
migration = importlib.import_module("workspace.files.migrations.0041_tag_color_hex")
tokens_to_hex = migration.tokens_to_hex
hex_to_tokens = migration.hex_to_tokens

User = get_user_model()


class TagColorMigrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mig", email="mig@test.com", password="x"
        )

    def _migrate(self):
        tokens_to_hex(apps, None)

    def test_every_picker_token_maps_to_a_css_color(self):
        tokens = {
            "error": "#ef4444",
            "orange-500": "#f97316",
            "warning": "#eab308",
            "success": "#22c55e",
            "info": "#3b82f6",
            "secondary": "#a855f7",
            "pink-500": "#ec4899",
            "accent": "#06b6d4",
        }
        for index, token in enumerate(tokens):
            Tag.objects.create(owner=self.user, name=f"tag-{index}", color=token)

        self._migrate()

        for index, (token, expected) in enumerate(tokens.items()):
            tag = Tag.objects.get(name=f"tag-{index}")
            self.assertEqual(tag.color, expected, f"{token} should become {expected}")

    def test_ghost_and_neutral_become_the_neutral_chip(self):
        Tag.objects.create(owner=self.user, name="a", color="ghost")
        Tag.objects.create(owner=self.user, name="b", color="neutral")

        self._migrate()

        self.assertEqual(Tag.objects.get(name="a").color, "")
        self.assertEqual(Tag.objects.get(name="b").color, "")

    def test_unknown_values_are_dropped_rather_than_rendered_as_css(self):
        """A token the picker never offered (set through the API) has no
        meaning as a CSS color — an invalid inline style would be worse."""
        Tag.objects.create(owner=self.user, name="odd", color="not-a-color")

        self._migrate()

        self.assertEqual(Tag.objects.get(name="odd").color, "")

    def test_colors_already_migrated_are_left_alone(self):
        """The migration must be safe to re-run: hex values survive."""
        Tag.objects.create(owner=self.user, name="hex", color="#ef4444")

        self._migrate()
        self._migrate()

        self.assertEqual(Tag.objects.get(name="hex").color, "#ef4444")

    def test_reverse_restores_tokens(self):
        Tag.objects.create(owner=self.user, name="red", color="error")
        Tag.objects.create(owner=self.user, name="none", color="ghost")

        self._migrate()
        hex_to_tokens(apps, None)

        self.assertEqual(Tag.objects.get(name="red").color, "error")
        self.assertEqual(Tag.objects.get(name="none").color, "ghost")
