import uuid as uuid_lib

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import File, FileTag, Tag

User = get_user_model()


class FileCardViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.client.force_login(self.user)
        self.note = File.objects.create(
            name="My Note.md",
            node_type=File.NodeType.FILE,
            type="markdown",
            owner=self.user,
            content=ContentFile(b"# Heading\n\nFirst line here", name="note.md"),
        )
        tag = Tag.objects.create(owner=self.user, name="project")
        FileTag.objects.create(file=self.note, tag=tag)

    def test_card_renders_title_tags_and_first_line(self):
        resp = self.client.get(f"/files/{self.note.uuid}/card")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "My Note.md")
        self.assertContains(resp, "project")
        self.assertContains(resp, "First line here")
        self.assertContains(resp, f"/notes?file={self.note.uuid}")

    def test_card_404_without_access(self):
        other = User.objects.create_user(username="bob", password="pw")
        their_note = File.objects.create(
            name="secret.md",
            node_type=File.NodeType.FILE,
            type="markdown",
            owner=other,
            content=ContentFile(b"hidden", name="secret.md"),
        )
        resp = self.client.get(f"/files/{their_note.uuid}/card")
        self.assertEqual(resp.status_code, 404)

    def test_card_404_for_missing(self):
        resp = self.client.get(f"/files/{uuid_lib.uuid4()}/card")
        self.assertEqual(resp.status_code, 404)
