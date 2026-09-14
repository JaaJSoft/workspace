from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.files.models import File, FileShare, FileShareLink

User = get_user_model()


class PropertiesPanelShareLinksTests(TestCase):
    """GET /files/properties/<uuid> - the "Public links" section.

    Public links exist for files and folders alike (a folder link can even be
    an upload drop box), so the owner must see them on both node types, and
    the section must stay visible when there is nothing to list so the owner
    can reach the link manager from the panel.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username="alice", email="alice@example.com", password="pass"
        )
        self.other = User.objects.create_user(
            username="bob", email="bob@example.com", password="pass"
        )
        self.client.login(username="alice", password="pass")
        self.folder = File.objects.create(
            owner=self.owner, name="Inbox", node_type=File.NodeType.FOLDER
        )
        self.file = File.objects.create(
            owner=self.owner, name="doc.txt", node_type=File.NodeType.FILE
        )

    def _properties_body(self, node):
        response = self.client.get(f"/files/properties/{node.uuid}")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_folder_owner_sees_public_links(self):
        link = FileShareLink.objects.create(
            file=self.folder, created_by=self.owner, mode=FileShareLink.Mode.DROP
        )

        body = self._properties_body(self.folder)

        self.assertIn("Public links", body)
        self.assertIn(link.token, body)
        self.assertIn("Upload only", body)

    def test_folder_without_links_offers_the_link_manager(self):
        body = self._properties_body(self.folder)

        self.assertIn("Public links", body)
        self.assertIn("No public links", body)
        self.assertIn("Manage links", body)

    def test_file_without_links_offers_the_link_manager(self):
        body = self._properties_body(self.file)

        self.assertIn("No public links", body)
        self.assertIn("Manage links", body)

    def test_non_owner_does_not_see_public_links(self):
        FileShareLink.objects.create(file=self.folder, created_by=self.owner)
        FileShare.objects.create(
            file=self.folder, shared_by=self.owner, shared_with=self.other
        )
        self.client.login(username="bob", password="pass")

        body = self._properties_body(self.folder)

        self.assertNotIn("Public links", body)
