"""Admin surfaces for quotas, and the audit command."""

from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from workspace.files.models import GroupStorageQuota, UserStorageQuota
from workspace.files.services import FileService

User = get_user_model()

KB = 1024


class QuotaAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        cls.group = Group.objects.create(name="Design")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_user_quota_changelist_renders(self):
        UserStorageQuota.objects.create(user=self.admin, quota_bytes=5 * KB)
        response = self.client.get(reverse("admin:files_userstoragequota_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "root")

    def test_group_quota_changelist_renders(self):
        GroupStorageQuota.objects.create(group=self.group, quota_bytes=5 * KB)
        response = self.client.get(reverse("admin:files_groupstoragequota_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Design")

    def test_the_user_change_page_still_renders_with_the_inline(self):
        response = self.client.get(
            reverse("admin:auth_user_change", args=[self.admin.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "quota_bytes")

    def test_the_group_change_page_still_renders_with_the_inline(self):
        response = self.client.get(
            reverse("admin:auth_group_change", args=[self.group.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "quota_bytes")

    def test_the_user_changelist_still_renders(self):
        self.assertEqual(
            self.client.get(reverse("admin:auth_user_changelist")).status_code, 200
        )


class StorageQuotaReportTests(TestCase):
    def setUp(self):
        # Created while the default (effectively unlimited) quota applies -
        # the lowered limit below is only for the report call itself.
        self.user = User.objects.create_user(username="hoarder", password="pw")
        FileService.create_file(
            self.user, "a.bin", content=ContentFile(b"x" * (3 * KB), name="a.bin")
        )

    @override_settings(STORAGE_QUOTA_BYTES=2 * KB)
    def test_the_report_lists_a_bucket_over_its_limit(self):
        out = StringIO()
        with self.assertRaises(SystemExit) as caught:
            call_command("storage_quota_report", "--over", stdout=out)
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("hoarder", out.getvalue())

    @override_settings(STORAGE_QUOTA_BYTES=2 * KB)
    def test_the_report_exits_zero_when_everyone_fits(self):
        UserStorageQuota.objects.create(user=self.user, quota_bytes=10 * KB)
        out = StringIO()
        call_command("storage_quota_report", "--over", stdout=out)
        self.assertNotIn("hoarder", out.getvalue())
