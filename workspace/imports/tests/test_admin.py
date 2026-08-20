from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workspace.imports.models import ImportConnection, ImportJob, ImportJobItem

User = get_user_model()


class ImportsAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        cls.connection = ImportConnection.objects.create(
            owner=cls.admin, provider="webdav", label="Nextcloud"
        )
        cls.job = ImportJob.objects.create(
            connection=cls.connection,
            status=ImportJob.Status.FAILED,
            kinds=["files"],
            error="401 Unauthorized",
        )
        cls.item = ImportJobItem.objects.create(
            job=cls.job,
            kind="files",
            remote_id="/Documents/report.pdf",
            status=ImportJobItem.Status.FAILED,
            error="timeout",
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_connection_change_list_renders(self):
        response = self.client.get(reverse("admin:imports_importconnection_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nextcloud")

    def test_job_change_list_renders_with_status_badge(self):
        response = self.client.get(reverse("admin:imports_importjob_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "failed")

    def test_job_item_change_list_renders(self):
        response = self.client.get(reverse("admin:imports_importjobitem_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "report.pdf")

    def test_jobs_cannot_be_added_or_edited_by_hand(self):
        self.assertEqual(
            self.client.get(reverse("admin:imports_importjob_add")).status_code, 403
        )
        self.assertEqual(
            self.client.get(reverse("admin:imports_importjobitem_add")).status_code,
            403,
        )
