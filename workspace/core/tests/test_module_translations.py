"""Modules and palette commands are named in the reader's language.

The registry is filled once, at startup, so its names are lazy: each page and
each API answer resolves them in the language of whoever asks.
"""

import json
import re

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import translation

from workspace.core.module_registry import registry
from workspace.users.services.settings import set_setting

User = get_user_model()


class ModuleNamesLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="pass")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _names(self, response, script_id):
        """The names a page hands its scripts, as rendered in its language."""
        match = re.search(
            rf'<script id="{script_id}" type="application/json">(.*?)</script>',
            response.content.decode(),
            re.S,
        )
        return [entry["name"] for entry in json.loads(match.group(1))]

    def test_modules_and_commands_render_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        modules = self._names(response, "workspace-modules-data")
        self.assertIn("Calendrier", modules)
        self.assertIn("Fichiers", modules)
        self.assertNotIn("Calendar", modules)
        self.assertIn("Fichiers récents", self._names(response, "workspace-commands"))

    def test_modules_and_commands_stay_english_by_default(self):
        response = self.client.get("/")
        self.assertIn("Calendar", self._names(response, "workspace-modules-data"))
        self.assertIn("Recent files", self._names(response, "workspace-commands"))

    def test_modules_api_answers_each_reader_in_their_language(self):
        set_setting(self.user, "core", "language", "fr")
        french = self.client.get("/api/v1/modules").json()["results"]
        self.assertIn("Calendrier", [module["name"] for module in french])

        reader = User.objects.create_user(username="dana", password="pass")
        self.client.force_login(reader)
        english = self.client.get("/api/v1/modules").json()["results"]
        self.assertIn("Calendar", [module["name"] for module in english])
        self.assertNotIn("Calendrier", [module["name"] for module in english])

    def test_palette_search_matches_the_translated_name(self):
        with translation.override("fr"):
            found = [
                str(command.name)
                for command in registry.search_commands("fichiers réc")
            ]
        self.assertEqual(found, ["Fichiers récents"])


class OfflinePageLanguageTests(TestCase):
    """The service worker caches this page while it installs, with the
    session of whoever installed it: the page is in their language."""

    def tearDown(self):
        cache.clear()

    def test_offline_page_renders_in_the_reader_language(self):
        user = User.objects.create_user(username="camille", password="pass")
        set_setting(user, "core", "language", "fr")
        self.client.force_login(user)
        response = self.client.get("/offline")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<html lang="fr">')
        self.assertContains(response, "Vous êtes hors ligne")
        self.assertContains(response, "Réessayer")

    def test_offline_page_is_english_without_a_session(self):
        response = self.client.get("/offline")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You're offline")
