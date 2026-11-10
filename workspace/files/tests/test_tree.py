"""Unit tests for the tree services, with the names and content writes they rely on."""

import os
import posixpath
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase

from workspace.common.tests.media import local_storage_only
from workspace.files.models import File
from workspace.files.services import trash, tree
from workspace.files.services.content_writes import (
    replace_content_storage,
    update_content,
)
from workspace.files.services.locking import Relocated
from workspace.files.services.names import check_name_available

User = get_user_model()


class TestCreateFile(TestCase):
    """Tests for tree.create_file()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="svcuser", email="svc@test.com", password="pass"
        )

    def test_basic_creation(self):
        f = tree.create_file(self.user, "readme.txt", mime_type="text/plain")
        self.assertEqual(f.name, "readme.txt")
        self.assertEqual(f.node_type, File.NodeType.FILE)
        self.assertEqual(f.owner, self.user)
        self.assertIsNone(f.size)

    def test_creation_with_content(self):
        content = ContentFile(b"hello world", name="hello.txt")
        f = tree.create_file(self.user, "hello.txt", content=content)
        self.assertEqual(f.size, 11)
        self.assertTrue(f.content)

    def test_mime_auto_detection(self):
        content = ContentFile(
            b'{"name": "test", "version": 1, "items": [1, 2, 3]}',
            name="data.json",
        )
        f = tree.create_file(self.user, "data.json", content=content)
        self.assertIn("json", f.mime_type)

    def test_explicit_mime_overrides(self):
        content = ContentFile(b"data", name="file.bin")
        f = tree.create_file(
            self.user, "file.bin", content=content, mime_type="application/custom"
        )
        self.assertEqual(f.mime_type, "application/custom")

    def test_creation_under_parent(self):
        folder = tree.create_folder(self.user, "docs")
        f = tree.create_file(
            self.user, "note.txt", parent=folder, mime_type="text/plain"
        )
        self.assertEqual(f.parent, folder)


class TestCreateFolder(TestCase):
    """Tests for tree.create_folder()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="svcuser2", email="svc2@test.com", password="pass"
        )

    def test_basic_creation(self):
        folder = tree.create_folder(self.user, "Photos")
        self.assertEqual(folder.name, "Photos")
        self.assertEqual(folder.node_type, File.NodeType.FOLDER)
        self.assertEqual(folder.owner, self.user)

    def test_nested_folder(self):
        parent = tree.create_folder(self.user, "Root")
        child = tree.create_folder(self.user, "Sub", parent=parent)
        self.assertEqual(child.parent, parent)
        self.assertEqual(child.path, "Root/Sub")

    def test_with_icon_and_color(self):
        folder = tree.create_folder(
            self.user, "Work", icon="briefcase", color="#ff0000"
        )
        self.assertEqual(folder.icon, "briefcase")
        self.assertEqual(folder.color, "#ff0000")


class TestCreateFolderOnStorage(TestCase):
    """Verify create_folder creates directories on FileSystemStorage."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="storageuser", email="storage@test.com", password="pass"
        )

    def test_root_folder_created_on_disk(self):
        tree.create_folder(self.user, "Photos")
        self.assertTrue(default_storage.is_dir("files/users/storageuser/Photos"))

    def test_nested_folder_created_on_disk(self):
        parent = tree.create_folder(self.user, "Documents")
        tree.create_folder(self.user, "Work", parent=parent)
        self.assertTrue(
            default_storage.is_dir("files/users/storageuser/Documents/Work")
        )

    def test_deeply_nested_folder(self):
        a = tree.create_folder(self.user, "A")
        b = tree.create_folder(self.user, "B", parent=a)
        tree.create_folder(self.user, "C", parent=b)
        self.assertTrue(default_storage.is_dir("files/users/storageuser/A/B/C"))

    def test_silently_skipped_for_unsupported_backend(self):
        """A backend without local paths (S3-like) must not break folder creation."""
        with patch(
            "workspace.files.services.storage_ops.default_storage.path",
            side_effect=NotImplementedError,
        ):
            folder = tree.create_folder(self.user, "Ignored")

        self.assertEqual(folder.name, "Ignored")
        self.assertEqual(folder.node_type, File.NodeType.FOLDER)


class TestRegisterDiskFile(TestCase):
    """Tests for tree.register_disk_file()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="svcuser3", email="svc3@test.com", password="pass"
        )

    def test_sets_content_name_directly(self):
        f = tree.register_disk_file(
            self.user,
            "report.pdf",
            None,
            "files/users/svcuser3/report.pdf",
            mime_type="application/pdf",
            size=1024,
        )
        self.assertEqual(f.content.name, "files/users/svcuser3/report.pdf")
        self.assertEqual(f.size, 1024)
        self.assertEqual(f.mime_type, "application/pdf")

    def test_infers_mime_when_not_provided(self):
        f = tree.register_disk_file(
            self.user,
            "photo.jpg",
            None,
            "files/users/svcuser3/photo.jpg",
        )
        self.assertIn("image", f.mime_type)


class TestRename(TestCase):
    """Tests for tree.rename()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="svcuser4", email="svc4@test.com", password="pass"
        )

    def test_rename_file_updates_name(self):
        content = ContentFile(b"data", name="old.txt")
        f = tree.create_file(self.user, "old.txt", content=content)
        tree.rename(f, "new.txt")
        f.refresh_from_db()
        self.assertEqual(f.name, "new.txt")

    def test_rename_to_a_name_without_extension_keeps_the_blob_at_that_name(self):
        """The blob lives at the node's tree path, the name it was given."""
        content = ContentFile(b"data", name="notes.txt")
        f = tree.create_file(self.user, "notes.txt", content=content)

        tree.rename(f, "README")

        f.refresh_from_db()
        self.assertEqual(f.content.name, f"files/users/{self.user.username}/README")
        with f.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"data")

    def test_rename_noop_when_same_name(self):
        f = tree.create_file(self.user, "same.txt", mime_type="text/plain")
        result = tree.rename(f, "same.txt")
        self.assertEqual(result.name, "same.txt")

    def test_rename_preserves_mime_type(self):
        """Rename does not re-infer MIME type because content didn't change."""
        content = ContentFile(b"hello", name="notes.txt")
        f = tree.create_file(self.user, "notes.txt", content=content)
        self.assertEqual(f.mime_type, "text/plain")
        tree.rename(f, "notes.md")
        f.refresh_from_db()
        self.assertEqual(f.mime_type, "text/plain")

    def test_rename_folder_updates_name(self):
        folder = tree.create_folder(self.user, "OldFolder")
        tree.rename(folder, "NewFolder")
        folder.refresh_from_db()
        self.assertEqual(folder.name, "NewFolder")

    def test_rename_nested_same_named_folder_only_rewrites_target_segment(self):
        # Layout: Docs/Sub/Docs/file.txt -- the inner "Docs" shares its name
        # with the outer "Docs". Renaming the inner one must rewrite only the
        # last segment of the storage path, not the first one matching "Docs".
        outer = tree.create_folder(self.user, "Docs")
        sub = tree.create_folder(self.user, "Sub", parent=outer)
        inner = tree.create_folder(self.user, "Docs", parent=sub)
        f = tree.create_file(
            self.user,
            "file.txt",
            parent=inner,
            content=ContentFile(b"data", name="file.txt"),
        )
        self.assertEqual(
            f.content.name,
            f"files/users/{self.user.username}/Docs/Sub/Docs/file.txt",
        )

        tree.rename(inner, "New")
        f.refresh_from_db()
        self.assertEqual(
            f.content.name,
            f"files/users/{self.user.username}/Docs/Sub/New/file.txt",
        )


class TestUpdateContent(TestCase):
    """Tests for update_content()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="svcuser5", email="svc5@test.com", password="pass"
        )

    def test_replaces_content(self):
        original = ContentFile(b"old", name="file.txt")
        f = tree.create_file(self.user, "file.txt", content=original)
        self.assertEqual(f.size, 3)

        new_content = ContentFile(b"new content here", name="file.txt")
        update_content(f, new_content)
        f.refresh_from_db()
        self.assertEqual(f.size, 16)

    def test_updates_mime_type(self):
        original = ContentFile(b"text", name="file.txt")
        f = tree.create_file(self.user, "file.txt", content=original)

        new_content = ContentFile(
            b'{"name": "test", "version": 1, "items": [1, 2, 3]}',
            name="file.json",
        )
        update_content(f, new_content, name="file.json")
        f.refresh_from_db()
        self.assertIn("json", f.mime_type)

    def test_resets_has_thumbnail(self):
        """A new content payload must invalidate any cached thumbnail."""
        f = tree.create_file(
            self.user,
            "photo.png",
            content=ContentFile(b"old", name="photo.png"),
        )
        f.has_thumbnail = True
        f.save(update_fields=["has_thumbnail"])

        update_content(f, ContentFile(b"new bytes", name="photo.png"))
        f.refresh_from_db()
        self.assertFalse(f.has_thumbnail)

    def test_bumps_updated_at(self):
        """`updated_at` must advance after a content update."""
        f = tree.create_file(
            self.user,
            "doc.txt",
            content=ContentFile(b"a", name="doc.txt"),
        )
        original_updated_at = f.updated_at

        update_content(f, ContentFile(b"bb", name="doc.txt"))
        f.refresh_from_db()
        self.assertGreater(f.updated_at, original_updated_at)

    def test_replaces_bytes_on_disk(self):
        """Reading the file back must return the new bytes, not the old."""
        f = tree.create_file(
            self.user,
            "doc.txt",
            content=ContentFile(b"old", name="doc.txt"),
        )
        update_content(f, ContentFile(b"fresh content", name="doc.txt"))
        f.refresh_from_db()
        with f.content.storage.open(f.content.name, "rb") as fh:
            self.assertEqual(fh.read(), b"fresh content")

    def test_a_storage_failure_takes_the_row_with_it(self):
        """The row and the bytes commit together or not at all.

        The row is written first so a refused write never touches storage, and
        that ordering only holds up if the reverse is true too - a row already
        pointing at bytes that failed to land would leave the file claiming a
        hash and a size its blob does not have.
        """
        f = tree.create_file(
            self.user,
            "doc.txt",
            content=ContentFile(b"old", name="doc.txt"),
        )
        original_hash, original_size = f.content_hash, f.size

        storage = File._meta.get_field("content").storage
        with (
            patch.object(storage, "replace", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            update_content(
                f, ContentFile(b"the new bytes", name="doc.txt"), name="doc.txt"
            )

        stored = File.objects.get(pk=f.pk)
        self.assertEqual(stored.content_hash, original_hash)
        self.assertEqual(stored.size, original_size)

    def _dying_storage_write(self, partial):
        """Patch the disk so a write lands *partial* bytes, then fails.

        A transfer cut off mid-flight, which is the case the staged write
        exists for: written in place, the target would be left holding
        neither version.
        """
        real_write = os.write
        writes = []

        def write_then_die(fd, data):
            if writes:
                raise OSError("connection reset mid-transfer")
            writes.append(data)
            return real_write(fd, bytes(data)[: len(partial)])

        return patch("workspace.common.storage.local.os.write", write_then_die)

    @local_storage_only
    def test_a_write_that_dies_halfway_leaves_the_previous_bytes_whole(self):
        """The path holds one complete version or the other, never a stump.

        The row rolls back to the version it described before, so bytes that
        are neither version leave it pointing at content that never existed -
        a mismatch no backfill can repair, unlike a hash it can recompute.
        """
        f = tree.create_file(
            self.user,
            "doc.txt",
            content=ContentFile(b"the original bytes", name="doc.txt"),
        )
        original_hash = f.content_hash

        with self._dying_storage_write(b"a new and lon"), self.assertRaises(OSError):
            update_content(
                f, ContentFile(b"a new and longer body", name="doc.txt"), name="doc.txt"
            )

        stored = File.objects.get(pk=f.pk)
        self.assertEqual(stored.content_hash, original_hash)
        with stored.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"the original bytes")

    @local_storage_only
    def test_a_failed_write_leaves_no_staging_file_behind(self):
        f = tree.create_file(
            self.user,
            "doc.txt",
            content=ContentFile(b"the original bytes", name="doc.txt"),
        )

        with self._dying_storage_write(b"half"), self.assertRaises(OSError):
            update_content(
                f, ContentFile(b"a new body", name="doc.txt"), name="doc.txt"
            )

        self.assertEqual(self._blobs_beside(f), ["doc.txt"])

    def test_a_successful_write_leaves_no_staging_file_behind(self):
        f = tree.create_file(
            self.user,
            "doc.txt",
            content=ContentFile(b"old", name="doc.txt"),
        )
        update_content(f, ContentFile(b"new bytes", name="doc.txt"), name="doc.txt")

        self.assertEqual(self._blobs_beside(f), ["doc.txt"])

    def _blobs_beside(self, f):
        directory = posixpath.dirname(f.content.name)
        return sorted(entry.name for entry in default_storage.scan(directory))

    def test_the_returned_instance_needs_no_refresh(self):
        """The row write bypasses ``save()``, so the instance follows by hand.

        Callers serialize what comes back without re-reading it - the
        upload-replace response, WOPI's ``LastModifiedTime`` - so a field left
        holding its pre-write value stays invisible until a user reads a stale
        answer.
        """
        f = tree.create_file(
            self.user,
            "doc.txt",
            content=ContentFile(b"old", name="doc.txt"),
        )
        before = f.updated_at

        update_content(f, ContentFile(b"the new bytes", name="doc.txt"), name="doc.txt")

        self.assertEqual(f.size, 13)
        self.assertFalse(f.has_thumbnail)
        self.assertGreater(f.updated_at, before)
        with f.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"the new bytes")
        stored = File.objects.get(pk=f.pk)
        self.assertEqual(f.content_hash, stored.content_hash)
        self.assertEqual(f.content.name, stored.content.name)

    def test_mime_type_override_takes_precedence(self):
        """Passing `mime_type=` skips inference."""
        f = tree.create_file(
            self.user,
            "data.bin",
            content=ContentFile(b"x", name="data.bin"),
        )
        update_content(
            f,
            ContentFile(b"y", name="data.bin"),
            mime_type="application/x-custom",
        )
        f.refresh_from_db()
        self.assertEqual(f.mime_type, "application/x-custom")


class TestReplaceContentStorage(TestCase):
    """Tests for replace_content_storage()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="svcuser_rcs",
            email="svc_rcs@test.com",
            password="pass",
        )

    def test_updates_size_and_mime_type(self):
        f = tree.create_file(
            self.user,
            "note.md",
            content=ContentFile(b"old", name="note.md"),
        )
        replace_content_storage(
            f,
            storage_path="custom/path/note.md",
            size=4096,
            content_hash="0" * 64,
        )
        f.refresh_from_db()
        self.assertEqual(f.size, 4096)
        self.assertIn("markdown", f.mime_type)

    def test_resets_has_thumbnail(self):
        f = tree.create_file(
            self.user,
            "photo.png",
            content=ContentFile(b"old", name="photo.png"),
        )
        f.has_thumbnail = True
        f.save(update_fields=["has_thumbnail"])

        replace_content_storage(
            f,
            storage_path="custom/path/photo.png",
            size=10,
            content_hash="0" * 64,
        )
        f.refresh_from_db()
        self.assertFalse(f.has_thumbnail)

    def test_bumps_updated_at(self):
        f = tree.create_file(
            self.user,
            "doc.txt",
            content=ContentFile(b"a", name="doc.txt"),
        )
        original_updated_at = f.updated_at

        replace_content_storage(
            f,
            storage_path="custom/path/doc.txt",
            size=2,
            content_hash="0" * 64,
        )
        f.refresh_from_db()
        self.assertGreater(f.updated_at, original_updated_at)

    def test_points_content_name_at_storage_path(self):
        f = tree.create_file(
            self.user,
            "doc.txt",
            content=ContentFile(b"a", name="doc.txt"),
        )
        replace_content_storage(
            f,
            storage_path="dav/streamed/doc.txt",
            size=11,
            content_hash="0" * 64,
        )
        f.refresh_from_db()
        self.assertEqual(f.content.name, "dav/streamed/doc.txt")

    def test_refused_for_a_file_moved_since_it_was_loaded(self):
        """The caller staged its bytes under a path derived from the row as it
        loaded it; a move that committed while the claim waited empties it."""
        folder = tree.create_folder(self.user, "Docs")
        f = tree.create_file(
            self.user,
            "doc.txt",
            parent=folder,
            content=ContentFile(b"a", name="doc.txt"),
        )
        loaded = File.objects.get(pk=f.pk)
        tree.rename(folder, "Archive")

        with self.assertRaises(Relocated):
            replace_content_storage(
                loaded,
                storage_path=loaded.content.name,
                size=1,
                content_hash="0" * 64,
            )

        f.refresh_from_db()
        self.assertEqual(f.content.name, "files/users/svcuser_rcs/Archive/doc.txt")

    def test_refused_for_a_file_trashed_since_it_was_loaded(self):
        """The trash leaves the tree path as it was and moves the blob all the
        same: the caller's bytes are staged under a name that is free again."""
        f = tree.create_file(
            self.user,
            "doc.txt",
            content=ContentFile(b"a", name="doc.txt"),
        )
        loaded = File.objects.get(pk=f.pk)
        trash.soft_delete(f, acting_user=self.user)

        with self.assertRaises(Relocated):
            replace_content_storage(
                loaded,
                storage_path=loaded.content.name,
                size=1,
                content_hash="0" * 64,
            )

        f.refresh_from_db()
        self.assertEqual(f.content.name, f"trash/users/svcuser_rcs/{f.uuid}/doc.txt")


class TestCopy(TestCase):
    """Tests for tree.copy()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="svcuser6", email="svc6@test.com", password="pass"
        )

    def test_copy_file(self):
        content = ContentFile(b"hello", name="doc.txt")
        original = tree.create_file(
            self.user, "doc.txt", content=content, mime_type="text/plain"
        )
        copied = tree.copy(original, None, self.user)
        self.assertNotEqual(copied.pk, original.pk)
        self.assertEqual(copied.name, "doc (Copy).txt")
        self.assertEqual(copied.size, original.size)

    def test_copy_file_to_different_folder_keeps_name(self):
        content = ContentFile(b"hello", name="doc.txt")
        original = tree.create_file(self.user, "doc.txt", content=content)
        target = tree.create_folder(self.user, "Other")
        copied = tree.copy(original, target, self.user)
        self.assertEqual(copied.name, "doc.txt")
        self.assertEqual(copied.parent, target)

    def test_copy_folder_recursive(self):
        folder = tree.create_folder(self.user, "Src")
        content = ContentFile(b"data", name="child.txt")
        tree.create_file(self.user, "child.txt", parent=folder, content=content)
        sub = tree.create_folder(self.user, "SubDir", parent=folder)
        tree.create_file(
            self.user,
            "deep.txt",
            parent=sub,
            content=ContentFile(b"deep", name="deep.txt"),
        )

        copied = tree.copy(folder, None, self.user)
        self.assertEqual(copied.name, "Src (Copy)")
        # Check children were copied
        children = File.objects.filter(parent=copied, deleted_at__isnull=True)
        self.assertEqual(children.count(), 2)
        child_names = set(children.values_list("name", flat=True))
        self.assertIn("child.txt", child_names)
        self.assertIn("SubDir", child_names)

    def test_copy_name_conflict_increments(self):
        folder = tree.create_folder(self.user, "Src")
        original = tree.create_folder(self.user, "A", parent=folder)
        tree.create_folder(self.user, "A")
        tree.create_folder(self.user, "A (Copy)")

        # Both "A" and "A (Copy)" are taken at the root.
        copied = tree.copy(original, None, self.user)
        self.assertEqual(copied.name, "A (Copy 2)")


class TestCheckNameAvailable(TestCase):
    """Tests for check_name_available()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="svcuser7", email="svc7@test.com", password="pass"
        )

    def test_raises_on_duplicate_file(self):
        tree.create_file(self.user, "doc.txt", mime_type="text/plain")
        with self.assertRaises(ValueError):
            check_name_available(
                self.user,
                None,
                "doc.txt",
                File.NodeType.FILE,
            )

    def test_case_insensitive(self):
        tree.create_file(self.user, "Doc.TXT", mime_type="text/plain")
        with self.assertRaises(ValueError):
            check_name_available(
                self.user,
                None,
                "doc.txt",
                File.NodeType.FILE,
            )

    def test_excludes_self_on_update(self):
        f = tree.create_file(self.user, "doc.txt", mime_type="text/plain")
        # Should not raise when excluding self
        check_name_available(
            self.user,
            None,
            "doc.txt",
            File.NodeType.FILE,
            exclude_pk=f.pk,
        )

    def test_ignores_soft_deleted(self):
        f = tree.create_file(self.user, "deleted.txt", mime_type="text/plain")
        trash.trash_subtree(f)
        # Should not raise since original is soft-deleted
        check_name_available(
            self.user,
            None,
            "deleted.txt",
            File.NodeType.FILE,
        )

    def test_folders_are_checked_too(self):
        # Two folders of the same name in one parent map to one directory on
        # storage, and files dropped in either would overwrite each other.
        tree.create_folder(self.user, "MyFolder")
        with self.assertRaises(ValueError):
            check_name_available(
                self.user,
                None,
                "MyFolder",
                File.NodeType.FOLDER,
            )

    def test_a_file_cannot_take_a_sibling_folders_name(self):
        # Same directory on storage: the write used to fail with a bare
        # IsADirectoryError escaping as a 500.
        tree.create_folder(self.user, "Docs")
        with self.assertRaises(ValueError):
            check_name_available(
                self.user,
                None,
                "Docs",
                File.NodeType.FILE,
            )


class TestValidateMoveTarget(TestCase):
    """Tests for tree.validate_move_target()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="svcuser8", email="svc8@test.com", password="pass"
        )
        self.other_user = User.objects.create_user(
            username="otheruser", email="other@test.com", password="pass"
        )

    def test_move_to_none_is_valid(self):
        folder = tree.create_folder(self.user, "A")
        tree.validate_move_target(folder, None)  # should not raise

    def test_cannot_move_into_self(self):
        folder = tree.create_folder(self.user, "A")
        with self.assertRaises(ValueError):
            tree.validate_move_target(folder, folder)

    def test_cannot_move_into_descendant(self):
        parent = tree.create_folder(self.user, "Parent")
        child = tree.create_folder(self.user, "Child", parent=parent)
        grandchild = tree.create_folder(self.user, "GrandChild", parent=child)
        with self.assertRaises(ValueError):
            tree.validate_move_target(parent, grandchild)

    def test_cannot_move_to_other_user_folder(self):
        folder = tree.create_folder(self.user, "Mine")
        other_folder = tree.create_folder(self.other_user, "Theirs")
        with self.assertRaises(ValueError):
            tree.validate_move_target(folder, other_folder)

    def test_move_file_to_valid_folder(self):
        f = tree.create_file(self.user, "doc.txt", mime_type="text/plain")
        target = tree.create_folder(self.user, "Target")
        tree.validate_move_target(f, target)  # should not raise


class TestMoveOnStorage(TestCase):
    """Tests for tree.move() with physical storage operations."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="moveuser", email="move@test.com", password="pass"
        )

    def _is_dir(self, *parts):
        return default_storage.is_dir("/".join(["files/users/moveuser", *parts]))

    def _move(self, node, parent):
        # Object storage drops what a move copied once its transaction commits.
        with self.captureOnCommitCallbacks(execute=True):
            tree.move(node, parent)

    def test_move_folder_moves_directory_on_disk(self):
        src = tree.create_folder(self.user, "Src")
        dest = tree.create_folder(self.user, "Dest")
        child = tree.create_folder(self.user, "Child", parent=src)

        self.assertTrue(self._is_dir("Src", "Child"))

        self._move(child, dest)

        self.assertFalse(self._is_dir("Src", "Child"))
        self.assertTrue(self._is_dir("Dest", "Child"))

    def test_move_folder_to_root(self):
        parent = tree.create_folder(self.user, "Parent")
        child = tree.create_folder(self.user, "Child", parent=parent)

        self.assertTrue(self._is_dir("Parent", "Child"))

        self._move(child, None)

        self.assertFalse(self._is_dir("Parent", "Child"))
        self.assertTrue(self._is_dir("Child"))

    def test_move_folder_updates_descendant_content_names(self):
        src = tree.create_folder(self.user, "Src")
        dest = tree.create_folder(self.user, "Dest")
        f = tree.create_file(
            self.user,
            "doc.txt",
            parent=src,
            content=ContentFile(b"hello", name="doc.txt"),
        )

        old_content_name = f.content.name.replace("\\", "/")
        self.assertTrue(old_content_name.startswith("files/users/moveuser/Src/"))

        tree.move(src, dest)

        f.refresh_from_db()
        new_content_name = f.content.name.replace("\\", "/")
        self.assertTrue(
            new_content_name.startswith("files/users/moveuser/Dest/Src/"),
            f"Expected path starting with files/users/moveuser/Dest/Src/, got {new_content_name}",
        )

    def test_move_file_moves_on_disk(self):
        src = tree.create_folder(self.user, "Src")
        dest = tree.create_folder(self.user, "Dest")
        f = tree.create_file(
            self.user,
            "doc.txt",
            parent=src,
            content=ContentFile(b"hello", name="doc.txt"),
        )

        old_name = f.content.name
        self.assertTrue(default_storage.is_file(old_name))

        self._move(f, dest)

        self.assertFalse(default_storage.is_file(old_name))
        self.assertTrue(default_storage.is_file(f.content.name))

    def test_move_file_to_root(self):
        folder = tree.create_folder(self.user, "Folder")
        f = tree.create_file(
            self.user,
            "doc.txt",
            parent=folder,
            content=ContentFile(b"hello", name="doc.txt"),
        )

        self._move(f, None)

        new_path = f.content.name.replace("\\", "/")
        self.assertNotIn("Folder", new_path)
        self.assertTrue(default_storage.is_file(f.content.name))

    def test_move_noop_when_same_parent(self):
        folder = tree.create_folder(self.user, "Folder")
        f = tree.create_file(
            self.user,
            "doc.txt",
            parent=folder,
            mime_type="text/plain",
        )
        # Should not raise or change anything
        tree.move(f, folder)
        f.refresh_from_db()
        self.assertEqual(f.parent, folder)
