"""The text viewer's AI menu needs a signed-in user: the AI endpoints refuse
an anonymous visitor of a public link."""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from workspace.files.models import FileShareLink
from workspace.files.services import tree

User = get_user_model()

AI_MENU = "aiAction('summarize')"


@override_settings(AI_API_KEY="test-key")
class TextViewerAiMenuTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="alice", password="pass")
        self.file = tree.create_file(
            self.owner, "notes.txt", None, content=ContentFile(b"hi", name="notes.txt")
        )

    def test_the_owner_gets_the_menu(self):
        self.client.force_login(self.owner)
        html = self.client.get(
            f"/files/view/{self.file.uuid}", HTTP_X_ALPINE_REQUEST="true"
        ).content.decode()
        self.assertIn(AI_MENU, html)

    def test_an_anonymous_visitor_of_a_link_does_not(self):
        link = FileShareLink.objects.create(file=self.file, created_by=self.owner)
        resp = self.client.get(f"/files/shared/{link.token}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("notes.txt", resp.content.decode())
        self.assertNotIn(AI_MENU, resp.content.decode())
