"""A share recipient reads a node's name, not the owner's folders above it.

The properties panel and the REST ``path`` field showed the full path, so a
recipient learnt the names of the private folders the shared file sits in.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from rest_framework.test import APITestCase

from workspace.files.models import FileFavorite, FileShare
from workspace.files.services import FileService

User = get_user_model()


class PathVisibilityTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.bob = User.objects.create_user(username="bob", password="pw")
        private = FileService.create_folder(self.alice, "MedicalPrivate")
        self.doc = FileService.create_file(
            self.alice,
            "note.md",
            parent=private,
            content=ContentFile(b"# t\n\nhello", name="note.md"),
        )

    def tearDown(self):
        cache.clear()

    def _share(self, permission):
        FileShare.objects.update_or_create(
            file=self.doc,
            shared_with=self.bob,
            defaults={"shared_by": self.alice, "permission": permission},
        )

    def test_a_recipients_properties_panel_shows_the_name_alone(self):
        for permission in ("ro", "rw"):
            with self.subTest(permission=permission):
                self._share(permission)
                self.client.force_login(self.bob)

                html = self.client.get(f"/files/properties/{self.doc.uuid}")

                self.assertEqual(html.status_code, 200)
                self.assertNotContains(html, "MedicalPrivate")
                self.assertContains(html, "/note.md")

    def test_a_recipients_api_path_is_the_name_alone(self):
        FileFavorite.objects.create(owner=self.bob, file=self.doc)
        for permission in ("ro", "rw"):
            with self.subTest(permission=permission):
                self._share(permission)
                self.client.force_login(self.bob)

                listing = self.client.get("/api/v1/files", {"favorites": "true"})

                self.assertEqual(listing.status_code, 200)
                self.assertEqual([item["path"] for item in listing.json()], ["note.md"])

    def test_the_owner_reads_the_full_path(self):
        self.client.force_login(self.alice)

        html = self.client.get(f"/files/properties/{self.doc.uuid}")
        self.assertContains(html, "/MedicalPrivate/note.md")
        api = self.client.get(f"/api/v1/files/{self.doc.uuid}")
        self.assertEqual(api.json()["path"], "MedicalPrivate/note.md")

    def test_a_group_member_reads_the_full_path(self):
        team = Group.objects.create(name="Team")
        self.alice.groups.add(team)
        self.bob.groups.add(team)
        root = FileService.create_folder(self.alice, "TeamRoot", group=team)
        sub = FileService.create_folder(self.alice, "Sub", parent=root)
        self.client.force_login(self.bob)

        html = self.client.get(f"/files/properties/{sub.uuid}")
        self.assertContains(html, "/TeamRoot/Sub")
        listing = self.client.get("/api/v1/files", {"parent": str(root.uuid)})
        self.assertEqual([item["path"] for item in listing.json()], ["TeamRoot/Sub"])
