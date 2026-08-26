"""Enforcement: every write path refuses what does not fit, and writes nothing."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

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
        import shutil
        import tempfile

        self._tmpdir = tempfile.mkdtemp()
        self._media_override = override_settings(MEDIA_ROOT=self._tmpdir)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

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
        child = self._in(folder, "a.bin", 2 * KB)
        self._in(folder, "b.bin", 2 * KB)
        with self.assertRaises(quota.QuotaExceeded):
            FileService.move(folder, self.design_root, acting_user=self.user)
        folder.refresh_from_db()
        self.assertEqual(folder.parent_id, self.personal.pk)
        self.assertIsNone(folder.group_id)
        child.refresh_from_db()
        self.assertIsNone(child.group_id)

    def test_a_refused_move_out_of_a_group_rolls_back_the_whole_subtree(self):
        """The rollback spans a group propagation and a descendant owner update.

        A descendant owned by someone else is what a partial failure corrupts:
        its owner would flip to the mover and its blob would move with it.
        """
        teammate = User.objects.create_user(username="teammate", password="pw")
        teammate.groups.add(self.group)
        folder = FileService.create_folder(self.user, "specs", parent=self.design_root)
        child = self._in(folder, "a.bin", 2 * KB, owner=teammate)
        blob_path = child.content.name
        UserStorageQuota.objects.create(user=self.user, quota_bytes=KB)

        with self.assertRaises(quota.QuotaExceeded):
            FileService.move(folder, self.personal, acting_user=self.user)

        folder.refresh_from_db()
        self.assertEqual(folder.parent_id, self.design_root.pk)
        self.assertEqual(folder.group_id, self.group.pk)
        self.assertEqual(folder.owner_id, self.user.pk)
        child.refresh_from_db()
        self.assertEqual(child.parent_id, folder.pk)
        self.assertEqual(child.group_id, self.group.pk)
        self.assertEqual(child.owner_id, teammate.pk)
        self.assertEqual(child.content.name, blob_path)
        self.assertTrue(default_storage.exists(blob_path))
        with child.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"x" * 2 * KB)

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


@override_settings(STORAGE_QUOTA_BYTES=KB)
class WebDavRefusedPutTests(TestCase):
    """A PUT refused before a byte is read must leave the tree as it was.

    ``do_PUT`` creates the empty row before calling ``begin_write``, then
    answers a raising ``begin_write`` with ``end_write(with_errors=True)``.
    """

    def setUp(self):
        import shutil
        import tempfile

        tmpdir = tempfile.mkdtemp()
        media = override_settings(MEDIA_ROOT=tmpdir)
        media.enable()
        self.addCleanup(media.disable)
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)

        self.user = User.objects.create_user(username="davput", password="pw")
        FileService.create_file(
            self.user, "used.bin", content=ContentFile(b"x" * KB, name="used.bin")
        )

    def _environ(self, content_length):
        return {
            "workspace.user": self.user,
            "wsgidav.provider": None,
            "CONTENT_LENGTH": str(content_length),
        }

    def _put(self, name, content_length):
        """Drive the create/begin/end sequence ``do_PUT`` uses for a new file."""
        from wsgidav.dav_error import DAVError

        from workspace.files.webdav.resources import RootCollection

        root = RootCollection("/", self._environ(content_length))
        res = root.create_empty_resource(name)
        with self.assertRaises(DAVError) as caught:
            res.begin_write(content_type="application/octet-stream")
        res.end_write(with_errors=True)
        return caught.exception

    def test_a_refused_new_file_put_leaves_no_row_behind(self):
        error = self._put("big.bin", 512)
        self.assertEqual(error.value, 507)
        self.assertFalse(
            File.objects.filter(name="big.bin").exists(),
            "the placeholder row must not survive a refused upload",
        )


@override_settings(STORAGE_QUOTA_BYTES=None)
class WebDavShrinkOverQuotaTests(TestCase):
    """WebDAV must honour the same escape hatch as REST: a smaller file fits."""

    def setUp(self):
        import shutil
        import tempfile

        tmpdir = tempfile.mkdtemp()
        media = override_settings(MEDIA_ROOT=tmpdir)
        media.enable()
        self.addCleanup(media.disable)
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)

        self.user = User.objects.create_user(username="davshrink", password="pw")
        self.file = FileService.create_file(
            self.user, "a.bin", content=ContentFile(b"x" * (8 * KB), name="a.bin")
        )
        FileService.create_file(
            self.user, "b.bin", content=ContentFile(b"x" * (4 * KB), name="b.bin")
        )
        # 12 KB used against a 10 KB limit: the bucket is 2 KB over.
        UserStorageQuota.objects.create(user=self.user, quota_bytes=10 * KB)

    def _resource(self, content_length):
        from workspace.files.webdav.resources import FileResource

        environ = {
            "workspace.user": self.user,
            "wsgidav.provider": None,
            "CONTENT_LENGTH": str(content_length),
        }
        return FileResource("/a.bin", environ, self.file)

    def test_shrinking_a_file_is_allowed_while_the_bucket_is_over_quota(self):
        res = self._resource(7 * KB)
        buf = res.begin_write(content_type="application/octet-stream")
        buf.write(b"x" * (7 * KB))
        buf.close()
        res.end_write(with_errors=False)
        self.file.refresh_from_db()
        self.assertEqual(self.file.size, 7 * KB)

    def test_growing_a_file_is_still_refused_while_over_quota(self):
        from wsgidav.dav_error import DAVError

        res = self._resource(9 * KB)
        with self.assertRaises(DAVError) as caught:
            res.begin_write(content_type="application/octet-stream")
        self.assertEqual(caught.exception.value, 507)


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

    def test_the_personal_root_aggregates_once_per_resource(self):
        """The root is the first resource every mount touches, and wsgidav
        asks both questions twice on an allprop PROPFIND."""
        from workspace.files.webdav.resources import RootCollection

        root = RootCollection("/", self._environ())
        with self.assertNumQueries(2):
            for _ in range(2):
                root.get_used_bytes()
                root.get_available_bytes()

    def test_an_unlimited_root_still_reports_what_it_holds(self):
        UserStorageQuota.objects.create(user=self.user, quota_bytes=None)
        from workspace.files.webdav.resources import RootCollection

        self.assertEqual(RootCollection("/", self._environ()).get_used_bytes(), KB)

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

    def test_a_group_root_aggregates_once_per_resource(self):
        """wsgidav asks both questions twice per resource on an allprop
        PROPFIND, and a listing holds one resource per group folder."""
        from workspace.files.webdav.resources import FolderResource

        GroupStorageQuota.objects.create(group=self.group, quota_bytes=4 * KB)
        res = FolderResource("/Design", self._environ(), self.group_root)
        with self.assertNumQueries(2):
            for _ in range(2):
                res.get_used_bytes()
                res.get_available_bytes()

    def test_a_sub_folder_reports_no_bucket(self):
        from workspace.files.webdav.resources import FolderResource

        sub = FileService.create_folder(self.user, "sub", parent=self.group_root)
        res = FolderResource("/Design/sub", self._environ(), sub)
        with self.assertNumQueries(0):
            self.assertIsNone(res.get_used_bytes())
            self.assertIsNone(res.get_available_bytes())


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


@override_settings(STORAGE_QUOTA_BYTES=4 * KB)
class ExtractEnforcementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="extractor", password="pw")

    def _archive(self, entries, name="bundle.zip"):
        import io
        import zipfile

        buf = io.BytesIO()
        # Deflated on purpose: the archive itself is a File and counts against
        # the same bucket, so a stored (uncompressed) 6 KB zip would be refused
        # by create_file before any test could extract it.
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry_name, payload in entries:
                zf.writestr(entry_name, payload)
        buf.seek(0)
        return FileService.create_file(
            self.user, name, content=ContentFile(buf.read(), name=name)
        )

    def test_extraction_stops_at_the_quota(self):
        from workspace.files.services.extract import extract_zip

        archive = self._archive(
            [("a.bin", b"x" * (3 * KB)), ("b.bin", b"y" * (3 * KB))]
        )
        with self.assertRaises(quota.QuotaExceeded):
            extract_zip(archive, None, acting_user=self.user)

    def test_a_refused_extraction_leaves_no_entry_behind(self):
        from unittest.mock import patch

        from workspace.files.services.extract import extract_zip

        archive = self._archive(
            [("a.bin", b"x" * (3 * KB)), ("b.bin", b"y" * (3 * KB))]
        )
        storage = File._meta.get_field("content").storage
        delete_calls = []
        orig_delete = storage.delete

        def tracking_delete(name):
            delete_calls.append(name)
            return orig_delete(name)

        with patch.object(storage, "delete", side_effect=tracking_delete):
            with self.assertRaises(quota.QuotaExceeded):
                extract_zip(archive, None, acting_user=self.user)

        self.assertFalse(File.objects.filter(name__in=["a.bin", "b.bin"]).exists())
        self.assertTrue(
            any("a.bin" in c for c in delete_calls),
            f"Expected a cleanup delete for the 'a.bin' blob, got: {delete_calls}",
        )
        self.assertFalse(default_storage.exists(delete_calls[0]))

    def test_an_archive_that_fits_still_extracts(self):
        from workspace.files.services.extract import extract_zip

        UserStorageQuota.objects.create(user=self.user, quota_bytes=100 * KB)
        archive = self._archive([("a.bin", b"x" * (3 * KB))])
        result = extract_zip(archive, None, acting_user=self.user)
        self.assertEqual(result["files_created"], 1)

    def _quota_queries(self, captured):
        return [
            q["sql"]
            for q in captured.captured_queries
            if "files_userstoragequota" in q["sql"]
            or 'SUM("files_file"."size")' in q["sql"]
        ]

    def test_the_loop_reads_the_bucket_once_whatever_the_entry_count(self):
        from workspace.files.services.extract import extract_zip

        UserStorageQuota.objects.create(user=self.user, quota_bytes=100 * KB)
        one = self._archive([("a.bin", b"x" * (3 * KB))])
        with CaptureQueriesContext(connection) as single:
            extract_zip(one, None, acting_user=self.user)

        five = self._archive(
            [(f"e{i}.bin", b"x" * (3 * KB)) for i in range(5)], name="bundle5.zip"
        )
        with CaptureQueriesContext(connection) as several:
            extract_zip(five, None, acting_user=self.user)

        self.assertEqual(len(self._quota_queries(single)), 2)
        self.assertEqual(
            len(self._quota_queries(several)), len(self._quota_queries(single))
        )

    def test_an_entry_larger_than_its_header_is_refused_on_the_real_bytes(self):
        """The loop tells create_file the bytes are already accounted for, so
        the check on what was actually decompressed is the only thing standing
        between an understated header and the bucket."""
        from unittest.mock import patch

        from workspace.files.services import extract as extract_module

        UserStorageQuota.objects.create(user=self.user, quota_bytes=100 * KB)
        archive = self._archive([("a.bin", b"x" * (3 * KB))])
        real = extract_module._stream_entry_to_tempfile

        def understated(zf, info, leaf, total_bytes, max_bytes):
            tmp, _ = real(zf, info, leaf, total_bytes, max_bytes)
            tmp.size = 200 * KB
            return tmp, total_bytes + 200 * KB

        with patch.object(extract_module, "_stream_entry_to_tempfile", understated):
            with self.assertRaises(quota.QuotaExceeded):
                extract_module.extract_zip(archive, None, acting_user=self.user)

        self.assertFalse(File.objects.filter(name="a.bin").exists())

    def test_the_fast_path_refuses_before_decompressing_the_entry(self):
        """The quota is charged before ``b.bin`` reaches
        ``_stream_entry_to_tempfile`` - decompression never happens for the
        entry that would be refused."""
        from unittest.mock import patch

        from workspace.files.services import extract as extract_module

        archive = self._archive(
            [("a.bin", b"x" * (3 * KB)), ("b.bin", b"y" * (3 * KB))]
        )
        with patch.object(
            extract_module,
            "_stream_entry_to_tempfile",
            wraps=extract_module._stream_entry_to_tempfile,
        ) as spy:
            with self.assertRaises(quota.QuotaExceeded):
                extract_module.extract_zip(archive, None, acting_user=self.user)

        self.assertEqual(spy.call_count, 1)
        self.assertEqual(spy.call_args.args[2], "a.bin")


@override_settings(STORAGE_QUOTA_BYTES=10 * KB)
class OverQuotaTrashTests(APITestCase):
    """A full bucket must not block the operations that empty it."""

    def setUp(self):
        self.user = User.objects.create_user(username="fullup", password="pw")
        self.client.force_authenticate(user=self.user)
        self.file = FileService.create_file(
            self.user, "a.bin", content=ContentFile(b"x" * (2 * KB), name="a.bin")
        )
        FileService.soft_delete(self.file)
        # Squeezed shut only once the bytes are in: create_file would have
        # refused them otherwise.
        UserStorageQuota.objects.create(user=self.user, quota_bytes=0)

    def test_restore_from_trash_is_never_refused(self):
        response = self.client.post(f"/api/v1/files/{self.file.uuid}/restore")
        self.assertEqual(response.status_code, 200)
        self.file.refresh_from_db()
        self.assertIsNone(self.file.deleted_at)

    def test_emptying_the_trash_frees_the_bucket(self):
        response = self.client.delete("/api/v1/files/trash/clean?force=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(quota.personal_usage(self.user), 0)

    def test_purging_a_single_item_is_never_refused(self):
        response = self.client.delete(f"/api/v1/files/{self.file.uuid}/purge")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(quota.personal_usage(self.user), 0)
