"""The notes page speaks the reader's language."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.users.services.settings import set_setting

User = get_user_model()


class NotesPageLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="camille", password="pass")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_notes_page_renders_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get("/notes")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sélectionnez ou créez une note")
        self.assertContains(response, "Mes notes")
        self.assertNotContains(response, "Select or create a note")
        self.assertNotContains(response, "My Notes")

    def test_notes_page_stays_english_by_default(self):
        response = self.client.get("/notes")
        self.assertContains(response, "Select or create a note")
        self.assertContains(response, "My Notes")

    def test_help_dialog_renders_in_french(self):
        set_setting(self.user, "core", "language", "fr")
        response = self.client.get("/notes")
        self.assertContains(response, "Dossiers et organisation")
        self.assertContains(response, "<code>**gras**</code>", html=False)
        self.assertNotContains(response, "Markdown Formatting")
