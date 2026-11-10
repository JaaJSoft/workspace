"""Where a trashed node's bytes sit, and what comes back on restore.

The invariant: a node's bytes are under ``trash/`` if and only if it is the
outermost trashed node of its chain. Everything below rides inside it, and
the live tree keeps mirroring exactly what the file browser shows.
"""

import posixpath

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase

from workspace.common.tests.media import IsolatedMediaRootMixin
from workspace.files.models import File
from workspace.files.services import trash, tree
from workspace.files.services.content_writes import update_content
from workspace.files.services.hard_delete import hard_delete_tree
from workspace.files.services.names import check_name_available

User = get_user_model()


class TrashStorageTestCase(IsolatedMediaRootMixin, TestCase):
    """Each move commits: object storage drops what it copied only then."""

    def _trash(self, node):
        with self.captureOnCommitCallbacks(execute=True):
            trash.soft_delete(node, acting_user=self.user)

    def _restore(self, node):
        with self.captureOnCommitCallbacks(execute=True):
            trash.restore(node, acting_user=self.user)

    def _purge(self, node):
        with self.captureOnCommitCallbacks(execute=True):
            hard_delete_tree(node)


class TrashStorageLayoutTests(TrashStorageTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="bob", password="pass")

    def _blobs(self):
        return sorted(
            blob.name
            for root in ("files", "trash")
            if default_storage.is_dir(root)
            for blob in default_storage.iter_blobs(root)
        )

    def _make_file(self, name, parent=None, content=b"bytes"):
        return tree.create_file(
            self.user, name, parent=parent, content=ContentFile(content, name=name)
        )

    def test_a_trashed_file_moves_under_its_own_trash_directory(self):
        f = self._make_file("report.pdf")
        self.assertEqual(self._blobs(), ["files/users/bob/report.pdf"])

        self._trash(f)

        f.refresh_from_db()
        self.assertEqual(f.content.name, f"trash/users/bob/{f.uuid}/report.pdf")
        self.assertEqual(self._blobs(), [f"trash/users/bob/{f.uuid}/report.pdf"])

    def test_a_save_that_waited_on_a_trash_lands_in_the_trash(self):
        """It loaded the file before the trash took it: its bytes go where the
        blob is now, not back into the live tree under a name that is free."""
        f = self._make_file("report.pdf", content=b"old")
        loaded = File.objects.get(pk=f.pk)
        self._trash(f)

        with self.captureOnCommitCallbacks(execute=True):
            update_content(
                loaded, ContentFile(b"new", name="report.pdf"), acting_user=self.user
            )

        trashed = f"trash/users/bob/{f.uuid}/report.pdf"
        f.refresh_from_db()
        self.assertEqual(f.content.name, trashed)
        self.assertEqual(self._blobs(), [trashed])
        with f.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"new")
        # The name the trash freed is still free.
        self._make_file("report.pdf", content=b"another")

    def test_a_save_that_waited_on_its_folders_trash_lands_inside_it(self):
        docs = tree.create_folder(self.user, "Docs")
        f = self._make_file("a.txt", parent=docs, content=b"old")
        loaded = File.objects.get(pk=f.pk)
        self._trash(docs)

        with self.captureOnCommitCallbacks(execute=True):
            update_content(
                loaded, ContentFile(b"new", name="a.txt"), acting_user=self.user
            )

        trashed = f"trash/users/bob/{docs.uuid}/Docs/a.txt"
        f.refresh_from_db()
        self.assertEqual(f.content.name, trashed)
        self.assertEqual(self._blobs(), [trashed])
        self._restore(docs)
        f.refresh_from_db()
        with f.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"new")

    def test_a_late_save_finds_a_file_trashed_before_its_folder(self):
        """A file trashed on its own keeps its trash directory when its folder
        is trashed after it, and the file that took its name rides inside the
        folder's: the late save must not land on that one."""
        docs = tree.create_folder(self.user, "Docs")
        first = self._make_file("a.txt", parent=docs, content=b"first")
        loaded = File.objects.get(pk=first.pk)
        self._trash(first)
        second = self._make_file("a.txt", parent=docs, content=b"second")
        self._trash(docs)

        with self.captureOnCommitCallbacks(execute=True):
            update_content(
                loaded, ContentFile(b"late", name="a.txt"), acting_user=self.user
            )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.content.name, f"trash/users/bob/{first.uuid}/a.txt")
        self.assertEqual(second.content.name, f"trash/users/bob/{docs.uuid}/Docs/a.txt")
        with first.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"late")
        with second.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"second")

    def test_the_name_on_disk_is_the_one_the_trash_view_shows(self):
        f = self._make_file("Quarterly Report.pdf")

        self._trash(f)

        f.refresh_from_db()
        self.assertEqual(posixpath.basename(f.content.name), "Quarterly Report.pdf")

    def test_a_trashed_folder_keeps_its_subtree_inside(self):
        folder = tree.create_folder(self.user, "Docs")
        sub = tree.create_folder(self.user, "Sub", parent=folder)
        deep = self._make_file("deep.txt", parent=sub)

        self._trash(folder)

        deep.refresh_from_db()
        self.assertEqual(
            deep.content.name,
            f"trash/users/bob/{folder.uuid}/Docs/Sub/deep.txt",
        )
        self.assertEqual(self._blobs(), [deep.content.name])

    def test_only_the_outermost_trashed_node_gets_a_directory(self):
        folder = tree.create_folder(self.user, "Docs")
        inner = self._make_file("deep.txt", parent=folder)

        self._trash(folder)

        inner.refresh_from_db()
        # Keyed by the folder's uuid, not the file's: the file rides inside.
        self.assertIn(str(folder.uuid), inner.content.name)
        self.assertNotIn(str(inner.uuid), inner.content.name)

    def test_restoring_puts_the_bytes_back_in_the_live_tree(self):
        folder = tree.create_folder(self.user, "Docs")
        deep = self._make_file("deep.txt", parent=folder)
        self._trash(folder)

        self._restore(folder)

        deep.refresh_from_db()
        self.assertEqual(deep.content.name, "files/users/bob/Docs/deep.txt")
        self.assertEqual(self._blobs(), ["files/users/bob/Docs/deep.txt"])

    def test_restoring_a_file_brings_its_folder_out_of_the_trash(self):
        folder = tree.create_folder(self.user, "Docs")
        wanted = self._make_file("wanted.txt", parent=folder)
        other = self._make_file("other.txt", parent=folder)
        self._trash(folder)

        # As the API does: the row is re-read, so it knows it is trashed.
        wanted.refresh_from_db()
        self._restore(wanted)

        folder.refresh_from_db()
        wanted.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNone(folder.deleted_at)
        self.assertIsNone(wanted.deleted_at)
        self.assertEqual(wanted.content.name, "files/users/bob/Docs/wanted.txt")
        # The sibling stays trashed, so it must leave the live tree it was
        # carried back into - on its own key this time.
        self.assertIsNotNone(other.deleted_at)
        self.assertEqual(other.content.name, f"trash/users/bob/{other.uuid}/other.txt")
        self.assertEqual(
            sorted(self._blobs()),
            sorted(
                [
                    "files/users/bob/Docs/wanted.txt",
                    f"trash/users/bob/{other.uuid}/other.txt",
                ]
            ),
        )

    def test_two_trashed_namesakes_keep_separate_directories(self):
        first = tree.create_folder(self.user, "Docs")
        old = self._make_file("deep.txt", parent=first, content=b"FIRST")
        self._trash(first)
        second = tree.create_folder(self.user, "Docs")
        new = self._make_file("deep.txt", parent=second, content=b"SECOND")
        self._trash(second)

        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual(
            old.content.name, f"trash/users/bob/{first.uuid}/Docs/deep.txt"
        )
        self.assertEqual(
            new.content.name, f"trash/users/bob/{second.uuid}/Docs/deep.txt"
        )

    def test_restoring_from_one_of_two_trashed_namesakes_picks_the_right_one(self):
        # Both folders carry the path "Docs", so resolving the trash root by
        # path hands back whichever row the lookup happens to keep - here the
        # younger one, whose directory has nothing to do with this file.
        first = tree.create_folder(self.user, "Docs")
        older = self._make_file("deep.txt", parent=first, content=b"FIRST")
        self._trash(first)
        second = tree.create_folder(self.user, "Docs")
        younger = self._make_file("deep.txt", parent=second, content=b"SECOND")
        self._trash(second)

        older.refresh_from_db()
        self._restore(older)

        older.refresh_from_db()
        younger.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(first.deleted_at)
        self.assertIsNotNone(second.deleted_at)
        with older.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"FIRST")
        with younger.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"SECOND")
        self.assertEqual(
            younger.content.name, f"trash/users/bob/{second.uuid}/Docs/deep.txt"
        )

    def test_restoring_onto_a_taken_name_renames_instead_of_overwriting(self):
        original = self._make_file("report.pdf", content=b"OLD")
        self._trash(original)
        rival = self._make_file("report.pdf", content=b"NEW")

        self._restore(original)

        original.refresh_from_db()
        rival.refresh_from_db()
        self.assertEqual(original.name, "report (Copy).pdf")
        self.assertEqual(rival.name, "report.pdf")
        self.assertEqual(
            self._blobs(),
            ["files/users/bob/report (Copy).pdf", "files/users/bob/report.pdf"],
        )

    def test_a_restored_folder_is_renamed_the_same_way(self):
        folder = tree.create_folder(self.user, "Docs")
        inner = self._make_file("deep.txt", parent=folder)
        self._trash(folder)
        tree.create_folder(self.user, "Docs")

        self._restore(folder)

        folder.refresh_from_db()
        inner.refresh_from_db()
        self.assertEqual(folder.name, "Docs (Copy)")
        self.assertEqual(inner.content.name, "files/users/bob/Docs (Copy)/deep.txt")

    def test_purging_a_trashed_node_removes_its_trash_directory(self):
        folder = tree.create_folder(self.user, "Docs")
        self._make_file("deep.txt", parent=folder)
        self._trash(folder)

        self._purge(folder)

        self.assertEqual(self._blobs(), [])
        self.assertFalse(default_storage.is_dir(f"trash/users/bob/{folder.uuid}"))

    def test_an_empty_folder_trashes_and_restores_without_a_directory(self):
        folder = tree.create_folder(self.user, "Empty")
        default_storage.remove_dir_if_empty("files/users/bob/Empty")

        self._trash(folder)
        self._restore(folder)

        folder.refresh_from_db()
        self.assertIsNone(folder.deleted_at)


class GroupTrashStorageTests(TrashStorageTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="alice", password="pass")
        self.group = Group.objects.create(name="Marketing")
        self.user.groups.add(self.group)
        self.root = tree.create_folder(self.user, "Marketing", group=self.group)

    def test_a_trashed_group_file_leaves_the_shared_tree(self):
        f = tree.create_file(
            self.user,
            "report.pdf",
            parent=self.root,
            content=ContentFile(b"shared", name="report.pdf"),
        )
        self.assertEqual(f.content.name, "files/groups/Marketing/report.pdf")

        self._trash(f)

        f.refresh_from_db()
        self.assertEqual(f.content.name, f"trash/groups/{f.uuid}/report.pdf")

    def test_a_teammate_can_take_the_freed_name(self):
        mate = User.objects.create_user(username="carol", password="pass")
        mate.groups.add(self.group)
        first = tree.create_file(
            self.user,
            "report.pdf",
            parent=self.root,
            content=ContentFile(b"MINE", name="report.pdf"),
        )
        self._trash(first)

        second = tree.create_file(
            mate,
            "report.pdf",
            parent=self.root,
            content=ContentFile(b"THEIRS", name="report.pdf"),
        )

        first.refresh_from_db()
        with first.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"MINE")
        with second.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"THEIRS")

    def test_deleting_the_group_trashes_its_files_out_of_the_tree(self):
        f = tree.create_file(
            self.user,
            "report.pdf",
            parent=self.root,
            content=ContentFile(b"shared", name="report.pdf"),
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.group.delete()

        f.refresh_from_db()
        self.assertIsNotNone(f.deleted_at)
        self.assertTrue(f.content.name.startswith("trash/"))
        self.assertTrue(default_storage.is_file(f.content.name))


class NameCollisionTests(TrashStorageTestCase):
    """The other two ways two rows used to land on one storage path."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="dave", password="pass")

    def test_two_folders_cannot_share_a_name_in_one_parent(self):
        tree.create_folder(self.user, "Docs")
        with self.assertRaises(ValueError):
            tree.create_folder(self.user, "Docs")

    def test_a_file_cannot_take_a_sibling_folders_name(self):
        tree.create_folder(self.user, "Docs")
        with self.assertRaises(ValueError):
            tree.create_file(self.user, "Docs", content=ContentFile(b"x", name="Docs"))

    def test_a_folder_cannot_take_a_sibling_files_name(self):
        tree.create_file(self.user, "Docs", content=ContentFile(b"x", name="Docs"))
        with self.assertRaises(ValueError):
            tree.create_folder(self.user, "Docs")

    def test_a_personal_root_does_not_compete_with_a_group_root(self):
        # files/users/<name>/Marketing and files/groups/Marketing are two
        # directories, so the same name in both is not a collision.
        group = Group.objects.create(name="Marketing")
        self.user.groups.add(group)
        tree.create_folder(self.user, "Marketing", group=group)

        check_name_available(self.user, None, "Marketing", File.NodeType.FOLDER)
        tree.create_folder(self.user, "Marketing")

        self.assertEqual(
            File.objects.filter(
                owner=self.user, name="Marketing", deleted_at__isnull=True
            ).count(),
            2,
        )

    def test_the_same_name_is_fine_in_two_different_folders(self):
        a = tree.create_folder(self.user, "A")
        b = tree.create_folder(self.user, "B")
        tree.create_folder(self.user, "Docs", parent=a)
        tree.create_folder(self.user, "Docs", parent=b)
        self.assertEqual(File.objects.filter(name="Docs", owner=self.user).count(), 2)

    def test_a_trashed_name_is_free_again(self):
        folder = tree.create_folder(self.user, "Docs")
        self._trash(folder)
        tree.create_folder(self.user, "Docs")
