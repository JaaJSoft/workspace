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

    def test_embeds_providers_connections_and_jobs_for_the_page_script(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("imports_ui:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="providers-data"')
        self.assertContains(response, '"slug": "nextcloud"')
        self.assertContains(response, 'id="connections-data"')
        self.assertContains(response, 'id="jobs-data"')
        self.assertContains(response, "imports/ui/js/imports.js")

    def test_lists_only_the_users_connections_and_jobs(self):
        other = User.objects.create_user(username="bob", password="pw")
        mine = ImportConnection.objects.create(
            owner=self.user, provider="webdav", label="Home Nextcloud"
        )
        theirs = ImportConnection.objects.create(
            owner=other, provider="webdav", label="Bob cloud"
        )
        ImportJob.objects.create(connection=mine, kinds=["files"])
        ImportJob.objects.create(connection=theirs, kinds=["files"])

        self.client.force_login(self.user)
        response = self.client.get(reverse("imports_ui:index"))

        self.assertContains(response, "Home Nextcloud")
        self.assertNotContains(response, "Bob cloud")
        self.assertEqual(len(response.context["jobs"]), 1)
        self.assertEqual(
            response.context["jobs"][0]["connection_label"], "Home Nextcloud"
        )

    def test_query_params_drive_the_wizard_and_the_highlight(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("imports_ui:index"), {"new": "1", "job": "not-a-uuid"}
        )
        self.assertTrue(response.context["open_wizard"])
        self.assertEqual(response.context["highlight_job"], "")
        job = ImportJob.objects.create(
            connection=ImportConnection.objects.create(
                owner=self.user, provider="webdav", label="x"
            ),
            kinds=["files"],
        )
        response = self.client.get(reverse("imports_ui:index"), {"job": str(job.uuid)})
        self.assertFalse(response.context["open_wizard"])
        self.assertEqual(response.context["highlight_job"], str(job.uuid))


class UserMenuEntryTests(TestCase):
    """Modules kept off the dashboard are reached from the navbar user menu."""

    def test_visible_user_gets_the_menu_entry(self):
        staff = User.objects.create_user(username="staff", password="pw", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("users_ui:settings"))
        self.assertContains(response, 'href="/imports"')

    def test_menu_entry_follows_module_visibility(self):
        regular = User.objects.create_user(username="regular", password="pw")
        self.client.force_login(regular)
        with self.settings(PREVIEW_VISIBILITY="staff"):
            response = self.client.get(reverse("users_ui:settings"))
        self.assertNotContains(response, 'href="/imports"')
