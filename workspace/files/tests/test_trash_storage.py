"""Where a trashed node's bytes sit, and what comes back on restore.

The invariant: a node's bytes are under ``trash/`` if and only if it is the
outermost trashed node of its chain. Everything below rides inside it, and
the live tree keeps mirroring exactly what the file browser shows.
"""

import os
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import File
from workspace.files.services import FileService

User = get_user_model()


class TrashStorageLayoutTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self._override = self.settings(MEDIA_ROOT=self.media_root)
        self._override.enable()
        self.user = User.objects.create_user(username="bob", password="pass")

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _blobs(self):
        found = []
        for directory, _subdirs, names in os.walk(self.media_root):
            for name in names:
                full = os.path.join(directory, name)
                found.append(os.path.relpath(full, self.media_root))
        return sorted(found)

    def _make_file(self, name, parent=None, content=b"bytes"):
        return FileService.create_file(
            self.user, name, parent=parent, content=ContentFile(content, name=name)
        )

    def test_a_trashed_file_moves_under_its_own_trash_directory(self):
        f = self._make_file("report.pdf")
        self.assertEqual(self._blobs(), ["files/users/bob/report.pdf"])

        FileService.soft_delete(f, acting_user=self.user)

        f.refresh_from_db()
        self.assertEqual(f.content.name, f"trash/users/bob/{f.uuid}/report.pdf")
        self.assertEqual(self._blobs(), [f"trash/users/bob/{f.uuid}/report.pdf"])

    def test_the_name_on_disk_is_the_one_the_trash_view_shows(self):
        f = self._make_file("Quarterly Report.pdf")

        FileService.soft_delete(f, acting_user=self.user)

        f.refresh_from_db()
        self.assertEqual(os.path.basename(f.content.name), "Quarterly Report.pdf")

    def test_a_trashed_folder_keeps_its_subtree_inside(self):
        folder = FileService.create_folder(self.user, "Docs")
        sub = FileService.create_folder(self.user, "Sub", parent=folder)
        deep = self._make_file("deep.txt", parent=sub)

        FileService.soft_delete(folder, acting_user=self.user)

        deep.refresh_from_db()
        self.assertEqual(
            deep.content.name,
            f"trash/users/bob/{folder.uuid}/Docs/Sub/deep.txt",
        )
        self.assertEqual(self._blobs(), [deep.content.name])

    def test_only_the_outermost_trashed_node_gets_a_directory(self):
        folder = FileService.create_folder(self.user, "Docs")
        inner = self._make_file("deep.txt", parent=folder)

        FileService.soft_delete(folder, acting_user=self.user)

        inner.refresh_from_db()
        # Keyed by the folder's uuid, not the file's: the file rides inside.
        self.assertIn(str(folder.uuid), inner.content.name)
        self.assertNotIn(str(inner.uuid), inner.content.name)

    def test_restoring_puts_the_bytes_back_in_the_live_tree(self):
        folder = FileService.create_folder(self.user, "Docs")
        deep = self._make_file("deep.txt", parent=folder)
        FileService.soft_delete(folder, acting_user=self.user)

        FileService.restore(folder, acting_user=self.user)

        deep.refresh_from_db()
        self.assertEqual(deep.content.name, "files/users/bob/Docs/deep.txt")
        self.assertEqual(self._blobs(), ["files/users/bob/Docs/deep.txt"])

    def test_restoring_a_file_brings_its_folder_out_of_the_trash(self):
        folder = FileService.create_folder(self.user, "Docs")
        wanted = self._make_file("wanted.txt", parent=folder)
        other = self._make_file("other.txt", parent=folder)
        FileService.soft_delete(folder, acting_user=self.user)

        # As the API does: the row is re-read, so it knows it is trashed.
        wanted.refresh_from_db()
        FileService.restore(wanted, acting_user=self.user)

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

    def test_restoring_onto_a_taken_name_renames_instead_of_overwriting(self):
        original = self._make_file("report.pdf", content=b"OLD")
        FileService.soft_delete(original, acting_user=self.user)
        rival = self._make_file("report.pdf", content=b"NEW")

        FileService.restore(original, acting_user=self.user)

        original.refresh_from_db()
        rival.refresh_from_db()
        self.assertEqual(original.name, "report (Copy).pdf")
        self.assertEqual(rival.name, "report.pdf")
        self.assertEqual(
            self._blobs(),
            ["files/users/bob/report (Copy).pdf", "files/users/bob/report.pdf"],
        )

    def test_a_restored_folder_is_renamed_the_same_way(self):
        folder = FileService.create_folder(self.user, "Docs")
        inner = self._make_file("deep.txt", parent=folder)
        FileService.soft_delete(folder, acting_user=self.user)
        FileService.create_folder(self.user, "Docs")

        FileService.restore(folder, acting_user=self.user)

        folder.refresh_from_db()
        inner.refresh_from_db()
        self.assertEqual(folder.name, "Docs (Copy)")
        self.assertEqual(inner.content.name, "files/users/bob/Docs (Copy)/deep.txt")

    def test_purging_a_trashed_node_removes_its_trash_directory(self):
        folder = FileService.create_folder(self.user, "Docs")
        self._make_file("deep.txt", parent=folder)
        FileService.soft_delete(folder, acting_user=self.user)

        FileService.hard_delete(folder, acting_user=self.user)

        self.assertEqual(self._blobs(), [])
        self.assertFalse(
            os.path.isdir(
                os.path.join(self.media_root, "trash", "users", "bob", str(folder.uuid))
            )
        )

    def test_an_empty_folder_trashes_and_restores_without_a_directory(self):
        folder = FileService.create_folder(self.user, "Empty")
        os.rmdir(os.path.join(self.media_root, "files", "users", "bob", "Empty"))

        FileService.soft_delete(folder, acting_user=self.user)
        FileService.restore(folder, acting_user=self.user)

        folder.refresh_from_db()
        self.assertIsNone(folder.deleted_at)


class GroupTrashStorageTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self._override = self.settings(MEDIA_ROOT=self.media_root)
        self._override.enable()
        self.user = User.objects.create_user(username="alice", password="pass")
        self.group = Group.objects.create(name="Marketing")
        self.user.groups.add(self.group)
        self.root = FileService.create_folder(self.user, "Marketing", group=self.group)

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def test_a_trashed_group_file_leaves_the_shared_tree(self):
        f = FileService.create_file(
            self.user,
            "report.pdf",
            parent=self.root,
            content=ContentFile(b"shared", name="report.pdf"),
        )
        self.assertEqual(f.content.name, "files/groups/Marketing/report.pdf")

        FileService.soft_delete(f, acting_user=self.user)

        f.refresh_from_db()
        self.assertEqual(f.content.name, f"trash/groups/{f.uuid}/report.pdf")

    def test_a_teammate_can_take_the_freed_name(self):
        mate = User.objects.create_user(username="carol", password="pass")
        mate.groups.add(self.group)
        first = FileService.create_file(
            self.user,
            "report.pdf",
            parent=self.root,
            content=ContentFile(b"MINE", name="report.pdf"),
        )
        FileService.soft_delete(first, acting_user=self.user)

        second = FileService.create_file(
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
        f = FileService.create_file(
            self.user,
            "report.pdf",
            parent=self.root,
            content=ContentFile(b"shared", name="report.pdf"),
        )

        self.group.delete()

        f.refresh_from_db()
        self.assertIsNotNone(f.deleted_at)
        self.assertTrue(f.content.name.startswith("trash/"))
        self.assertTrue(os.path.isfile(os.path.join(self.media_root, f.content.name)))


class NameCollisionTests(TestCase):
    """The other two ways two rows used to land on one storage path."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self._override = self.settings(MEDIA_ROOT=self.media_root)
        self._override.enable()
        self.user = User.objects.create_user(username="dave", password="pass")

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def test_two_folders_cannot_share_a_name_in_one_parent(self):
        FileService.create_folder(self.user, "Docs")
        with self.assertRaises(ValueError):
            FileService.create_folder(self.user, "Docs")

    def test_a_file_cannot_take_a_sibling_folders_name(self):
        FileService.create_folder(self.user, "Docs")
        with self.assertRaises(ValueError):
            FileService.create_file(
                self.user, "Docs", content=ContentFile(b"x", name="Docs")
            )

    def test_a_folder_cannot_take_a_sibling_files_name(self):
        FileService.create_file(
            self.user, "Docs", content=ContentFile(b"x", name="Docs")
        )
        with self.assertRaises(ValueError):
            FileService.create_folder(self.user, "Docs")

    def test_the_same_name_is_fine_in_two_different_folders(self):
        a = FileService.create_folder(self.user, "A")
        b = FileService.create_folder(self.user, "B")
        FileService.create_folder(self.user, "Docs", parent=a)
        FileService.create_folder(self.user, "Docs", parent=b)
        self.assertEqual(File.objects.filter(name="Docs", owner=self.user).count(), 2)

    def test_a_trashed_name_is_free_again(self):
        folder = FileService.create_folder(self.user, "Docs")
        FileService.soft_delete(folder, acting_user=self.user)
        FileService.create_folder(self.user, "Docs")
