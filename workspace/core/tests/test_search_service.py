"""Tests for workspace.core.services.search — the shared unified-search path.

The API view and the assistant's `search_everything` tool both go through
these helpers, so the visibility filter is pinned here rather than once per
caller.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.core.module_registry import CommandInfo, ModuleInfo
from workspace.core.services.search import search_commands, search_modules

User = get_user_model()


def _module(slug, preview=False):
    return ModuleInfo(
        name=slug.title(),
        slug=slug,
        description="",
        icon="i",
        color="c",
        url=f"/{slug}",
        preview=preview,
    )


def _command(slug):
    return CommandInfo(
        name=slug.title(),
        keywords=[],
        icon="i",
        color="c",
        url=f"/{slug}",
        kind="navigate",
        module_slug=slug,
    )


class SearchModulesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="alice", password="pass")

    @patch("workspace.core.services.search.registry")
    def test_short_query_never_reaches_the_providers(self, mock_registry):
        self.assertEqual(search_modules("a", self.user), [])
        self.assertEqual(search_modules("  ", self.user), [])
        mock_registry.search.assert_not_called()

    @patch("workspace.core.services.search.registry")
    def test_query_is_stripped_and_limit_forwarded_per_provider(self, mock_registry):
        mock_registry.search.return_value = []

        search_modules("  alpha  ", self.user, limit=4)

        mock_registry.search.assert_called_once_with("alpha", self.user, 4)

    @override_settings(PREVIEW_VISIBILITY="none")
    @patch("workspace.core.services.module_visibility.registry")
    @patch("workspace.core.services.search.registry")
    def test_hits_from_modules_the_user_cannot_see_are_dropped(
        self, mock_registry, mock_visibility_registry
    ):
        mock_registry.search.return_value = [
            {"module_slug": "files", "name": "doc"},
            {"module_slug": "lab", "name": "secret"},
        ]
        mock_visibility_registry.get.side_effect = lambda slug: _module(
            slug, preview=slug == "lab"
        )

        hits = search_modules("alpha", self.user)

        self.assertEqual([h["name"] for h in hits], ["doc"])


class SearchCommandsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="bob", password="pass")

    @patch("workspace.core.services.search.registry")
    def test_short_query_never_reaches_the_registry(self, mock_registry):
        self.assertEqual(search_commands("a", self.user), [])
        mock_registry.search_commands.assert_not_called()

    @override_settings(PREVIEW_VISIBILITY="none")
    @patch("workspace.core.services.module_visibility.registry")
    @patch("workspace.core.services.search.registry")
    def test_commands_are_serialized_and_filtered(
        self, mock_registry, mock_visibility_registry
    ):
        mock_registry.search_commands.return_value = [
            _command("files"),
            _command("lab"),
        ]
        mock_visibility_registry.get.side_effect = lambda slug: _module(
            slug, preview=slug == "lab"
        )

        commands = search_commands("fil", self.user)

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["module_slug"], "files")
        self.assertEqual(commands[0]["url"], "/files")
