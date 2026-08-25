"""Enforcement: every write path refuses what does not fit, and writes nothing."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from workspace.files.models import File, GroupStorageQuota, UserStorageQuota
from workspace.files.services import FileService, quota

User = get_user_model()

KB = 1024


@override_settings(STORAGE_QUOTA_BYTES=10 * KB)
class CheckWriteAllowedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="writer", password="pw")
        self.group = Group.objects.create(name="Design")

    def test_a_write_that_fits_is_allowed(self):
        quota.check_write_allowed(owner=self.user, group=None, additional_bytes=10 * KB)

    def test_one_byte_over_is_refused(self):
        with self.assertRaises(quota.QuotaExceeded):
            quota.check_write_allowed(
                owner=self.user, group=None, additional_bytes=10 * KB + 1
            )

    def test_a_shrinking_write_is_allowed_even_over_quota(self):
        UserStorageQuota.objects.create(user=self.user, quota_bytes=0)
        quota.check_write_allowed(owner=self.user, group=None, additional_bytes=-5)
        quota.check_write_allowed(owner=self.user, group=None, additional_bytes=0)

    def test_an_unlimited_group_accepts_anything(self):
        quota.check_write_allowed(
            owner=self.user, group=self.group, additional_bytes=10**12
        )

    def test_the_error_names_the_bucket(self):
        GroupStorageQuota.objects.create(group=self.group, quota_bytes=1)
        with self.assertRaises(quota.QuotaExceeded) as caught:
            quota.check_write_allowed(
                owner=self.user, group=self.group, additional_bytes=2
            )
        self.assertIn("Design", str(caught.exception.detail))
        self.assertEqual(caught.exception.status_code, 413)

    def test_the_personal_error_does_not_mention_a_group(self):
        with self.assertRaises(quota.QuotaExceeded) as caught:
            quota.check_write_allowed(
                owner=self.user, group=None, additional_bytes=11 * KB
            )
        self.assertIn("personal", str(caught.exception.detail).lower())


@override_settings(STORAGE_QUOTA_BYTES=10 * KB)
class CreateFileEnforcementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="creator", password="pw")
        self.group = Group.objects.create(name="Design")
        self.user.groups.add(self.group)
        self.group_root = FileService.create_folder(
            self.user, "Design", group=self.group
        )

    def test_an_upload_over_the_quota_is_refused(self):
        with self.assertRaises(quota.QuotaExceeded):
            FileService.create_file(
                self.user,
                "big.bin",
                content=ContentFile(b"x" * (10 * KB + 1), name="big.bin"),
            )

    def test_a_refused_upload_leaves_no_row_and_no_blob(self):
        before = set(File.objects.values_list("pk", flat=True))
        with self.assertRaises(quota.QuotaExceeded):
            FileService.create_file(
                self.user,
                "big.bin",
                content=ContentFile(b"x" * (10 * KB + 1), name="big.bin"),
            )
        self.assertEqual(set(File.objects.values_list("pk", flat=True)), before)
        self.assertFalse(
            default_storage.exists(f"files/users/{self.user.username}/big.bin")
        )

    def test_uploads_accumulate_until_the_quota_is_reached(self):
        FileService.create_file(
            self.user, "a.bin", content=ContentFile(b"x" * (6 * KB), name="a.bin")
        )
        with self.assertRaises(quota.QuotaExceeded):
            FileService.create_file(
                self.user, "b.bin", content=ContentFile(b"x" * (5 * KB), name="b.bin")
            )

    def test_a_full_personal_bucket_does_not_block_an_unlimited_group_folder(self):
        FileService.create_file(
            self.user, "a.bin", content=ContentFile(b"x" * (10 * KB), name="a.bin")
        )
        created = FileService.create_file(
            self.user,
            "team.bin",
            parent=self.group_root,
            content=ContentFile(b"x" * (50 * KB), name="team.bin"),
        )
        self.assertEqual(created.group_id, self.group.pk)

    def test_a_full_group_folder_blocks_a_user_with_room_to_spare(self):
        GroupStorageQuota.objects.create(group=self.group, quota_bytes=KB)
        with self.assertRaises(quota.QuotaExceeded):
            FileService.create_file(
                self.user,
                "team.bin",
                parent=self.group_root,
                content=ContentFile(b"x" * (2 * KB), name="team.bin"),
            )

    def test_a_folder_is_never_refused(self):
        FileService.create_file(
            self.user, "a.bin", content=ContentFile(b"x" * (10 * KB), name="a.bin")
        )
        FileService.create_folder(self.user, "still allowed")

    def test_register_disk_file_is_never_refused(self):
        """The bytes are already on disk; refusing here would strand a file
        nobody can see or delete."""
        UserStorageQuota.objects.create(user=self.user, quota_bytes=0)
        created = FileService.register_disk_file(
            self.user,
            "synced.bin",
            None,
            f"files/users/{self.user.username}/synced.bin",
            size=10 * KB,
        )
        self.assertIn(created.pk, File.objects.values_list("pk", flat=True))


@override_settings(STORAGE_QUOTA_BYTES=10 * KB)
class UpdateContentEnforcementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="updater", password="pw")
        self.file = FileService.create_file(
            self.user, "a.bin", content=ContentFile(b"x" * (9 * KB), name="a.bin")
        )

    def test_growing_past_the_quota_is_refused(self):
        with self.assertRaises(quota.QuotaExceeded):
            FileService.update_content(
                self.file, ContentFile(b"x" * (11 * KB), name="a.bin")
            )
        self.file.refresh_from_db()
        self.assertEqual(self.file.size, 9 * KB)

    def test_only_the_delta_counts(self):
        FileService.update_content(
            self.file, ContentFile(b"x" * (10 * KB), name="a.bin")
        )
        self.file.refresh_from_db()
        self.assertEqual(self.file.size, 10 * KB)

    def test_shrinking_is_allowed_while_over_quota(self):
        UserStorageQuota.objects.create(user=self.user, quota_bytes=KB)
        FileService.update_content(self.file, ContentFile(b"x", name="a.bin"))
        self.file.refresh_from_db()
        self.assertEqual(self.file.size, 1)

    def test_the_owner_of_the_target_is_charged_not_the_editor(self):
        """Writing into someone else's file must not be a way around a quota."""
        editor = User.objects.create_user(username="editor", password="pw")
        UserStorageQuota.objects.create(user=editor, quota_bytes=0)
        FileService.update_content(
            self.file, ContentFile(b"x" * (10 * KB), name="a.bin"), acting_user=editor
        )
        self.file.refresh_from_db()
        self.assertEqual(self.file.size, 10 * KB)


@override_settings(STORAGE_QUOTA_BYTES=10 * KB)
class CopyEnforcementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="copyquota", password="pw")
        self.file = FileService.create_file(
            self.user, "a.bin", content=ContentFile(b"x" * (6 * KB), name="a.bin")
        )
        self.folder = FileService.create_folder(self.user, "dest")

    def test_a_copy_that_would_double_past_the_quota_is_refused(self):
        with self.assertRaises(quota.QuotaExceeded):
            FileService.copy(self.file, self.folder, self.user)

    def test_a_refused_copy_creates_nothing(self):
        with self.assertRaises(quota.QuotaExceeded):
            FileService.copy(self.file, self.folder, self.user)
        self.assertFalse(File.objects.filter(parent=self.folder).exists())

    def test_a_copy_that_fits_is_allowed(self):
        UserStorageQuota.objects.create(user=self.user, quota_bytes=20 * KB)
        copied = FileService.copy(self.file, self.folder, self.user)
        self.assertEqual(copied.parent_id, self.folder.pk)


@override_settings(STORAGE_QUOTA_BYTES=10 * KB)
class MoveEnforcementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mover", password="pw")
        self.group = Group.objects.create(name="Design")
        self.other_group = Group.objects.create(name="Ops")
        self.user.groups.add(self.group, self.other_group)
        self.design_root = FileService.create_folder(
            self.user, "Design", group=self.group
        )
        self.ops_root = FileService.create_folder(
            self.user, "Ops", group=self.other_group
        )
        self.personal = FileService.create_folder(self.user, "mine")

    def _in(self, parent, name, size, owner=None):
        return FileService.create_file(
            owner or self.user,
            name,
            parent=parent,
            content=ContentFile(b"x" * size, name=name),
        )

    def test_personal_to_a_full_group_is_refused(self):
        GroupStorageQuota.objects.create(group=self.group, quota_bytes=KB)
        f = self._in(self.personal, "a.bin", 2 * KB)
        with self.assertRaises(quota.QuotaExceeded):
            FileService.move(f, self.design_root, acting_user=self.user)
        f.refresh_from_db()
        self.assertEqual(f.parent_id, self.personal.pk)
        self.assertIsNone(f.group_id)

    def test_group_to_personal_charges_the_mover(self):
        UserStorageQuota.objects.create(user=self.user, quota_bytes=KB)
        f = self._in(self.design_root, "a.bin", 2 * KB)
        with self.assertRaises(quota.QuotaExceeded):
            FileService.move(f, self.personal, acting_user=self.user)
        f.refresh_from_db()
        self.assertEqual(f.parent_id, self.design_root.pk)
        self.assertEqual(f.group_id, self.group.pk)

    def test_group_to_group_is_checked_against_the_destination(self):
        GroupStorageQuota.objects.create(group=self.other_group, quota_bytes=KB)
        f = self._in(self.design_root, "a.bin", 2 * KB)
        with self.assertRaises(quota.QuotaExceeded):
            FileService.move(f, self.ops_root, acting_user=self.user)
        f.refresh_from_db()
        self.assertEqual(f.group_id, self.group.pk)

    def test_a_move_inside_the_same_bucket_is_never_checked(self):
        # The file has to exist before the bucket is squeezed shut, otherwise
        # create_file refuses it and the move is never reached.
        f = self._in(None, "a.bin", 2 * KB)
        UserStorageQuota.objects.create(user=self.user, quota_bytes=0)
        FileService.move(f, self.personal, acting_user=self.user)
        f.refresh_from_db()
        self.assertEqual(f.parent_id, self.personal.pk)

    def test_a_folder_move_counts_the_whole_subtree(self):
        GroupStorageQuota.objects.create(group=self.group, quota_bytes=3 * KB)
        folder = FileService.create_folder(self.user, "batch", parent=self.personal)
        self._in(folder, "a.bin", 2 * KB)
        self._in(folder, "b.bin", 2 * KB)
        with self.assertRaises(quota.QuotaExceeded):
            FileService.move(folder, self.design_root, acting_user=self.user)
        folder.refresh_from_db()
        self.assertEqual(folder.parent_id, self.personal.pk)

    def test_a_trashed_descendant_travels_and_counts(self):
        GroupStorageQuota.objects.create(group=self.group, quota_bytes=3 * KB)
        folder = FileService.create_folder(self.user, "batch", parent=self.personal)
        self._in(folder, "a.bin", 2 * KB)
        trashed = self._in(folder, "b.bin", 2 * KB)
        FileService.soft_delete(trashed)
        with self.assertRaises(quota.QuotaExceeded):
            FileService.move(folder, self.design_root, acting_user=self.user)
