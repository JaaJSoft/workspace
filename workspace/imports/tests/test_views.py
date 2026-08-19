from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workspace.imports.models import ImportConnection, ImportJob

User = get_user_model()


class ImportsPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", password="pw", is_staff=True
        )

    def test_requires_login(self):
        response = self.client.get(reverse("imports_ui:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_renders_empty_states(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("imports_ui:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No connection yet")
        self.assertContains(response, "No import yet")

    def test_lists_only_the_users_connections_and_jobs(self):
        other = User.objects.create_user(username="bob", password="pw")
        mine = ImportConnection.objects.create(
            owner=self.user, provider="webdav", label="Home Nextcloud"
        )
        theirs = ImportConnection.objects.create(
            owner=other, provider="webdav", label="Bob cloud"
        )
        ImportJob.objects.create(owner=self.user, connection=mine, kinds=["files"])
        ImportJob.objects.create(owner=other, connection=theirs, kinds=["files"])

        self.client.force_login(self.user)
        response = self.client.get(reverse("imports_ui:index"))

        self.assertContains(response, "Home Nextcloud")
        self.assertNotContains(response, "Bob cloud")
        self.assertContains(response, "Pending")
