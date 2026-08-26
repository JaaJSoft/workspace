"""Quota resolution: what limit applies to a bucket, and where it comes from."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from workspace.files.models import GroupStorageQuota, UserStorageQuota
from workspace.files.services import quota

User = get_user_model()

MB = 1024 * 1024


@override_settings(STORAGE_QUOTA_BYTES=10 * MB)
class EffectiveQuotaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="quotauser", password="pw")
        self.group = Group.objects.create(name="Design")

    def test_user_without_a_row_falls_back_to_the_global_default(self):
        self.assertEqual(quota.effective_quota(self.user), 10 * MB)

    def test_user_row_overrides_the_global_default(self):
        UserStorageQuota.objects.create(user=self.user, quota_bytes=42 * MB)
        self.assertEqual(quota.effective_quota(self.user), 42 * MB)

    def test_user_row_without_a_value_means_unlimited(self):
        UserStorageQuota.objects.create(user=self.user, quota_bytes=None)
        self.assertIsNone(quota.effective_quota(self.user))

    def test_deleting_the_row_restores_the_fallback(self):
        row = UserStorageQuota.objects.create(user=self.user, quota_bytes=42 * MB)
        row.delete()
        self.assertEqual(quota.effective_quota(self.user), 10 * MB)

    def test_group_without_a_row_is_unlimited(self):
        self.assertIsNone(quota.effective_group_quota(self.group))

    def test_group_row_sets_the_limit(self):
        GroupStorageQuota.objects.create(group=self.group, quota_bytes=5 * MB)
        self.assertEqual(quota.effective_group_quota(self.group), 5 * MB)

    def test_group_row_without_a_value_means_unlimited(self):
        GroupStorageQuota.objects.create(group=self.group, quota_bytes=None)
        self.assertIsNone(quota.effective_group_quota(self.group))

    def test_resolution_accepts_a_primary_key(self):
        UserStorageQuota.objects.create(user=self.user, quota_bytes=7 * MB)
        self.assertEqual(quota.effective_quota(self.user.pk), 7 * MB)
        GroupStorageQuota.objects.create(group=self.group, quota_bytes=3 * MB)
        self.assertEqual(quota.effective_group_quota(self.group.pk), 3 * MB)
