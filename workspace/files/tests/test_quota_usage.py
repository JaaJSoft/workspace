"""Bucket totals: what counts, against which bucket, and who agrees on it."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.db.models import Sum
from django.test import TestCase, override_settings

from workspace.files.models import File
from workspace.files.services import FileService, quota

User = get_user_model()

MB = 1024 * 1024


class BucketUsageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="usage", password="pw")
        self.other = User.objects.create_user(username="other", password="pw")
        self.group = Group.objects.create(name="Design")
        self.user.groups.add(self.group)
        self.group_root = FileService.create_folder(
            self.user, "Design", group=self.group
        )

    def _upload(self, name, payload, parent=None, owner=None):
        return FileService.create_file(
            owner or self.user,
            name,
            parent=parent,
            content=ContentFile(payload, name=name),
        )

    def test_personal_usage_sums_live_personal_files(self):
        self._upload("a.txt", b"12345")
        self._upload("b.txt", b"123")
        self.assertEqual(quota.personal_usage(self.user), 8)

    def test_markdown_notes_count(self):
        self._upload("note.md", b"# hello")
        self.assertEqual(quota.personal_usage(self.user), 7)

    def test_trashed_files_keep_counting(self):
        f = self._upload("a.txt", b"12345")
        FileService.soft_delete(f)
        self.assertEqual(quota.personal_usage(self.user), 5)

    def test_hard_deleting_frees_the_bucket(self):
        f = self._upload("a.txt", b"12345")
        FileService.hard_delete(f)
        self.assertEqual(quota.personal_usage(self.user), 0)

    def test_group_files_count_against_the_group_only(self):
        self._upload("team.txt", b"1234567890", parent=self.group_root)
        self.assertEqual(quota.group_usage(self.group), 10)
        self.assertEqual(quota.personal_usage(self.user), 0)

    def test_folders_do_not_count(self):
        FileService.create_folder(self.user, "empty")
        self.assertEqual(quota.personal_usage(self.user), 0)

    def test_every_file_lands_in_exactly_one_bucket(self):
        self._upload("a.txt", b"12345")
        self._upload("note.md", b"# hello")
        trashed = self._upload("gone.txt", b"999")
        FileService.soft_delete(trashed)
        self._upload("team.txt", b"1234567890", parent=self.group_root)
        self._upload("theirs.txt", b"77", owner=self.other)

        buckets = (
            quota.personal_usage(self.user)
            + quota.personal_usage(self.other)
            + quota.group_usage(self.group)
        )
        whole_tree = File.objects.filter(node_type=File.NodeType.FILE).aggregate(
            total=Sum("size")
        )["total"]
        self.assertEqual(buckets, whole_tree)


@override_settings(STORAGE_QUOTA_BYTES=10 * MB)
class RemainingBytesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="remaining", password="pw")
        self.group = Group.objects.create(name="Design")

    def test_personal_remaining_is_quota_minus_usage(self):
        FileService.create_file(
            self.user, "a.txt", content=ContentFile(b"x" * 1024, name="a.txt")
        )
        self.assertEqual(
            quota.remaining_bytes(owner=self.user, group=None), 10 * MB - 1024
        )

    def test_unlimited_group_has_no_remaining_figure(self):
        self.assertIsNone(quota.remaining_bytes(owner=self.user, group=self.group))

    def test_bucket_state_labels_the_bucket(self):
        used, limit, label = quota.bucket_state(owner=self.user, group=None)
        self.assertEqual((used, limit), (0, 10 * MB))
        self.assertIn("personal", label.lower())
        used, limit, label = quota.bucket_state(owner=self.user, group=self.group)
        self.assertIsNone(limit)
        self.assertIn("Design", label)


class SurfacesAgreeTests(TestCase):
    """The gauge, the WebDAV report and the insights panel must not diverge."""

    def setUp(self):
        self.user = User.objects.create_user(username="surfaces", password="pw")
        FileService.create_file(
            self.user, "a.txt", content=ContentFile(b"x" * 2048, name="a.txt")
        )
        trashed = FileService.create_file(
            self.user, "b.txt", content=ContentFile(b"y" * 512, name="b.txt")
        )
        FileService.soft_delete(trashed)

    def test_storage_analysis_reports_the_bucket_usage(self):
        from workspace.files.services.storage_analysis import analyze_storage

        analysis = analyze_storage(self.user, None)
        self.assertEqual(analysis["quota_used"], quota.personal_usage(self.user))
        self.assertEqual(analysis["quota"], quota.effective_quota(self.user))

    def test_file_service_no_longer_exposes_its_own_total(self):
        self.assertFalse(hasattr(FileService, "storage_used"))


class UsageQueryCountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="counted", password="pw")
        self.group = Group.objects.create(name="Design")

    def test_each_bucket_total_is_a_single_aggregate(self):
        with self.assertNumQueries(1):
            quota.personal_usage(self.user)
        with self.assertNumQueries(1):
            quota.group_usage(self.group)

    def test_remaining_costs_one_lookup_and_one_aggregate(self):
        with self.assertNumQueries(2):
            quota.remaining_bytes(owner=self.user, group=None)

    def test_an_unlimited_bucket_skips_the_aggregate(self):
        with self.assertNumQueries(1):
            quota.remaining_bytes(owner=self.user, group=self.group)
