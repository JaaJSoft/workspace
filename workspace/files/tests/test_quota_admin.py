"""Admin surfaces for quotas, and the audit command."""

from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.template.defaultfilters import filesizeformat
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from workspace.files.models import GroupStorageQuota, UserStorageQuota
from workspace.files.services import FileService
from workspace.files.services.quota import group_usage, personal_usage

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

    def test_the_user_changelist_costs_the_same_with_one_row_or_many(self):
        UserStorageQuota.objects.create(user=self.admin, quota_bytes=5 * KB)
        url = reverse("admin:files_userstoragequota_changelist")
        with CaptureQueriesContext(connection) as one_row:
            self.client.get(url)
        for i in range(10):
            member = User.objects.create_user(username=f"m{i}", password="pw")
            FileService.create_file(
                member, "a.bin", content=ContentFile(b"x" * KB, name="a.bin")
            )
            UserStorageQuota.objects.create(user=member, quota_bytes=5 * KB)
        with CaptureQueriesContext(connection) as many_rows:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(many_rows), len(one_row))

    def test_the_group_changelist_costs_the_same_with_one_row_or_many(self):
        GroupStorageQuota.objects.create(group=self.group, quota_bytes=5 * KB)
        url = reverse("admin:files_groupstoragequota_changelist")
        with CaptureQueriesContext(connection) as one_row:
            self.client.get(url)
        for i in range(10):
            GroupStorageQuota.objects.create(
                group=Group.objects.create(name=f"g{i}"), quota_bytes=5 * KB
            )
        with CaptureQueriesContext(connection) as many_rows:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(many_rows), len(one_row))

    def test_the_changelist_reports_the_same_usage_as_the_helper(self):
        FileService.create_file(
            self.admin, "a.bin", content=ContentFile(b"x" * (3 * KB), name="a.bin")
        )
        UserStorageQuota.objects.create(user=self.admin, quota_bytes=5 * KB)
        response = self.client.get(reverse("admin:files_userstoragequota_changelist"))
        self.assertContains(
            response, f"{filesizeformat(personal_usage(self.admin.pk))} of "
        )

    def test_the_quota_change_page_reports_usage_without_an_annotation(self):
        """The change form builds its object outside the annotated changelist
        queryset, so the column has to fall back to the aggregate."""
        FileService.create_file(
            self.admin, "a.bin", content=ContentFile(b"x" * (3 * KB), name="a.bin")
        )
        row = UserStorageQuota.objects.create(user=self.admin, quota_bytes=5 * KB)
        response = self.client.get(
            reverse("admin:files_userstoragequota_change", args=[row.pk])
        )
        self.assertContains(
            response, f"{filesizeformat(personal_usage(self.admin.pk))} of "
        )

    def test_an_unlimited_row_reads_as_unlimited_on_the_changelist(self):
        FileService.create_file(
            self.admin, "a.bin", content=ContentFile(b"x" * KB, name="a.bin")
        )
        UserStorageQuota.objects.create(user=self.admin, quota_bytes=None)
        response = self.client.get(reverse("admin:files_userstoragequota_changelist"))
        self.assertContains(response, "used (unlimited)")

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
        with self.assertRaises(CommandError) as caught:
            call_command("storage_quota_report", "--over", stdout=out)
        self.assertEqual(caught.exception.returncode, 1)
        self.assertIn("hoarder", out.getvalue())

    @override_settings(STORAGE_QUOTA_BYTES=10 * KB)
    def test_the_report_reads_every_bucket_in_a_fixed_number_of_queries(self):
        Group.objects.create(name="Design")
        with self.assertNumQueries(6):
            call_command("storage_quota_report", stdout=StringIO())

        for i in range(5):
            member = User.objects.create_user(username=f"u{i}", password="pw")
            FileService.create_file(
                member, "a.bin", content=ContentFile(b"x" * KB, name="a.bin")
            )
            Group.objects.create(name=f"g{i}")
        with self.assertNumQueries(6):
            call_command("storage_quota_report", stdout=StringIO())

    @override_settings(STORAGE_QUOTA_BYTES=10 * KB)
    def test_the_report_totals_match_the_per_bucket_helpers(self):
        group = Group.objects.create(name="Design")
        self.user.groups.add(group)
        root = FileService.create_folder(self.user, "Design", group=group)
        FileService.create_file(
            self.user,
            "t.bin",
            parent=root,
            content=ContentFile(b"x" * KB, name="t.bin"),
        )
        UserStorageQuota.objects.create(user=self.user, quota_bytes=8 * KB)
        GroupStorageQuota.objects.create(group=group, quota_bytes=4 * KB)

        out = StringIO()
        call_command("storage_quota_report", stdout=out)
        report = out.getvalue()

        self.assertIn(filesizeformat(personal_usage(self.user.pk)), report)
        self.assertIn(filesizeformat(group_usage(group.pk)), report)
        self.assertIn(filesizeformat(8 * KB), report)
        self.assertIn(filesizeformat(4 * KB), report)

    @override_settings(STORAGE_QUOTA_BYTES=2 * KB)
    def test_a_user_without_an_override_falls_back_to_the_setting(self):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("storage_quota_report", "--over", stdout=out)
        self.assertIn(filesizeformat(2 * KB), out.getvalue())

    @override_settings(STORAGE_QUOTA_BYTES=2 * KB)
    def test_an_explicit_null_override_reads_as_unlimited(self):
        UserStorageQuota.objects.create(user=self.user, quota_bytes=None)
        out = StringIO()
        call_command("storage_quota_report", stdout=out)
        self.assertIn("unlimited", out.getvalue())

    @override_settings(STORAGE_QUOTA_BYTES=2 * KB)
    def test_the_report_exits_zero_when_everyone_fits(self):
        UserStorageQuota.objects.create(user=self.user, quota_bytes=10 * KB)
        out = StringIO()
        call_command("storage_quota_report", "--over", stdout=out)
        self.assertNotIn("hoarder", out.getvalue())
