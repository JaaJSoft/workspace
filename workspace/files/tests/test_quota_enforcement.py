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

    def test_a_copy_into_a_full_group_folder_is_refused(self):
        group = Group.objects.create(name="Design")
        self.user.groups.add(group)
        group_root = FileService.create_folder(self.user, "Design", group=group)
        GroupStorageQuota.objects.create(group=group, quota_bytes=1)
        with self.assertRaises(quota.QuotaExceeded):
            FileService.copy(self.file, group_root, self.user)
        self.assertFalse(File.objects.filter(parent=group_root).exists())

    def test_a_copy_into_a_group_folder_with_room_succeeds(self):
        group = Group.objects.create(name="Design")
        self.user.groups.add(group)
        group_root = FileService.create_folder(self.user, "Design", group=group)
        GroupStorageQuota.objects.create(group=group, quota_bytes=20 * KB)
        copied = FileService.copy(self.file, group_root, self.user)
        self.assertEqual(copied.parent_id, group_root.pk)
        self.assertEqual(copied.group_id, group.pk)


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

    def test_a_move_within_the_same_group_bucket_is_never_checked(self):
        # Same reasoning as test_a_move_inside_the_same_bucket_is_never_checked,
        # but both buckets are the same group instead of both personal.
        f = self._in(self.design_root, "a.bin", 2 * KB)
        GroupStorageQuota.objects.create(group=self.group, quota_bytes=0)
        sub = FileService.create_folder(self.user, "sub", parent=self.design_root)
        FileService.move(f, sub, acting_user=self.user)
        f.refresh_from_db()
        self.assertEqual(f.parent_id, sub.pk)


class WebDavWriteBufferTests(TestCase):
    """The buffer must refuse before the disk fills, not after."""

    def test_the_buffer_aborts_as_soon_as_the_ceiling_is_crossed(self):
        import os
        import tempfile

        from wsgidav.dav_error import DAVError

        from workspace.files.webdav.resources import _StreamingWriteBuffer

        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "upload.bin")
            buf = _StreamingWriteBuffer(target, 1024, max_bytes=10)
            buf.write(b"x" * 8)
            with self.assertRaises(DAVError) as caught:
                buf.write(b"x" * 8)
            self.assertEqual(caught.exception.value, 507)
            buf.abort()
            self.assertFalse(os.path.exists(target))
            self.assertEqual(
                [p for p in os.listdir(tmp) if p.endswith(".part")],
                [],
                "the partial upload must be cleaned up",
            )

    def test_no_ceiling_means_no_limit(self):
        import os
        import tempfile

        from workspace.files.webdav.resources import _StreamingWriteBuffer

        with tempfile.TemporaryDirectory() as tmp:
            buf = _StreamingWriteBuffer(os.path.join(tmp, "u.bin"), 1024)
            buf.write(b"x" * 100_000)
            buf.finalize()
            self.assertEqual(buf.size, 100_000)


@override_settings(STORAGE_QUOTA_BYTES=10 * KB)
class WebDavQuotaAdvertisingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="davuser", password="pw")
        self.group = Group.objects.create(name="Design")
        self.user.groups.add(self.group)
        self.group_root = FileService.create_folder(
            self.user, "Design", group=self.group
        )
        FileService.create_file(
            self.user, "a.bin", content=ContentFile(b"x" * KB, name="a.bin")
        )

    def _environ(self):
        # wsgidav._DAVResource.__init__ indexes environ["wsgidav.provider"]
        # unconditionally; a resource built for direct testing (no real
        # provider dispatch) still needs the key present.
        return {"workspace.user": self.user, "wsgidav.provider": None}

    def test_the_personal_root_reports_the_effective_quota(self):
        from workspace.files.webdav.resources import RootCollection

        root = RootCollection("/", self._environ())
        self.assertEqual(root.get_used_bytes(), KB)
        self.assertEqual(root.get_available_bytes(), 9 * KB)

    def test_an_unlimited_bucket_advertises_nothing(self):
        UserStorageQuota.objects.create(user=self.user, quota_bytes=None)
        from workspace.files.webdav.resources import RootCollection

        self.assertIsNone(RootCollection("/", self._environ()).get_available_bytes())

    def test_a_group_root_reports_the_group_bucket(self):
        from workspace.files.webdav.resources import FolderResource

        GroupStorageQuota.objects.create(group=self.group, quota_bytes=4 * KB)
        FileService.create_file(
            self.user,
            "team.bin",
            parent=self.group_root,
            content=ContentFile(b"x" * KB, name="team.bin"),
        )
        res = FolderResource("/Design", self._environ(), self.group_root)
        self.assertEqual(res.get_used_bytes(), KB)
        self.assertEqual(res.get_available_bytes(), 3 * KB)


class WebDavMoveCopyQuotaTests(TestCase):
    """A refused MOVE/COPY must reach the client as 507, and a refused MOVE
    must never leave a file's storage renamed out from under its row."""

    def setUp(self):
        import shutil
        import tempfile

        self._tmpdir = tempfile.mkdtemp()
        self._media_override = override_settings(MEDIA_ROOT=self._tmpdir)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

        self.user = User.objects.create_user(username="davmoveq", password="pw")
        self.group = Group.objects.create(name="Design")
        self.user.groups.add(self.group)
        self.design_root = FileService.create_folder(
            self.user, "Design", group=self.group
        )
        GroupStorageQuota.objects.create(group=self.group, quota_bytes=0)

    def _make_resource(self, file_obj, path="/a.bin"):
        from workspace.files.webdav.resources import FileResource

        environ = {"workspace.user": self.user, "wsgidav.provider": None}
        return FileResource(path, environ, file_obj)

    def test_a_refused_move_answers_507_and_leaves_the_file_intact(self):
        from wsgidav.dav_error import DAVError

        file_obj = FileService.create_file(
            self.user, "a.bin", content=ContentFile(b"payload", name="a.bin")
        )
        res = self._make_resource(file_obj)

        with self.assertRaises(DAVError) as caught:
            res.copy_move_single("/Design/b.bin", is_move=True)
        self.assertEqual(caught.exception.value, 507)

        file_obj.refresh_from_db()
        self.assertEqual(file_obj.name, "a.bin")
        self.assertIsNone(file_obj.parent_id)
        with file_obj.content.open("rb") as f:
            self.assertEqual(f.read(), b"payload")

    def test_a_refused_copy_answers_507(self):
        from wsgidav.dav_error import DAVError

        file_obj = FileService.create_file(
            self.user, "a.bin", content=ContentFile(b"payload", name="a.bin")
        )
        res = self._make_resource(file_obj)

        with self.assertRaises(DAVError) as caught:
            res.copy_move_single("/Design/copy.bin", is_move=False)
        self.assertEqual(caught.exception.value, 507)
