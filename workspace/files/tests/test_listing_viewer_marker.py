"""A listing row names the viewer that opens it, which the slideshow reads to
warm the next image."""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.services import tree

User = get_user_model()


class ListingViewerMarkerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.client.force_login(self.user)

    def test_a_row_names_its_viewer_and_a_folder_none(self):
        text = tree.create_file(
            self.user, "notes.txt", None, content=ContentFile(b"hi", name="notes.txt")
        )
        folder = tree.create_folder(self.user, "Docs")

        html = self.client.get("/files").content.decode()

        row = html[html.index(f'data-uuid="{text.uuid}"') :]
        self.assertIn('data-viewer="text"', row[: row.index(">")])
        row = html[html.index(f'data-uuid="{folder.uuid}"') :]
        self.assertIn('data-viewer=""', row[: row.index(">")])
