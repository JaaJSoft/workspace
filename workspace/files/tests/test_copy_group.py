"""A copy made inside a group folder must belong to that group.

``copy_node`` used to build the destination row without a group: the copy
stayed personal, its blob landed under the copier's own storage root, and no
other member of the group could see it.
"""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from workspace.files.models import File
from workspace.files.services import FileService

User = get_user_model()


class CopyIntoGroupFolderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="copier", password="pw")
        self.teammate = User.objects.create_user(username="teammate", password="pw")
        self.group = Group.objects.create(name="Design")
        self.user.groups.add(self.group)
        self.teammate.groups.add(self.group)
        self.group_root = FileService.create_folder(
            self.user, "Design", group=self.group
        )

    def _file(self, name="note.txt", parent=None):
        return FileService.create_file(
            self.user, name, parent=parent, content=ContentFile(b"hello", name=name)
        )

    def test_copy_joins_the_destination_group(self):
        copied = FileService.copy(self._file(), self.group_root, self.user)
        copied.refresh_from_db()
        self.assertEqual(copied.group_id, self.group.pk)

    def test_copied_blob_lands_under_the_group_storage_root(self):
        copied = FileService.copy(self._file(), self.group_root, self.user)
        self.assertTrue(
            copied.content.name.startswith("files/groups/"),
            f"blob stored at {copied.content.name}",
        )

    def test_copy_is_visible_to_another_group_member(self):
        copied = FileService.copy(self._file(), self.group_root, self.user)
        visible = FileService.user_group_files_qs(self.teammate)
        self.assertTrue(visible.filter(pk=copied.pk).exists())

    def test_folder_copy_propagates_the_group_to_descendants(self):
        folder = FileService.create_folder(self.user, "docs")
        self._file("child.txt", parent=folder)
        copied = FileService.copy(folder, self.group_root, self.user)
        child = File.objects.get(parent=copied, name="child.txt")
        self.assertEqual(child.group_id, self.group.pk)

    def test_copy_out_of_a_group_into_the_personal_tree_clears_the_group(self):
        source = FileService.create_file(
            self.user,
            "shared.txt",
            parent=self.group_root,
            content=ContentFile(b"hello", name="shared.txt"),
        )
        personal = FileService.create_folder(self.user, "mine")
        copied = FileService.copy(source, personal, self.user)
        copied.refresh_from_db()
        self.assertIsNone(copied.group_id)


class CopyNameCollisionInGroupTests(TestCase):
    """A copy must dodge every name in the destination folder, not just its own.

    Names are unique per folder within a group, so a copy that only avoids the
    copier's own names picks a teammate's name, lands on the teammate's
    group-scoped storage path and - the storage allows overwrites - truncates
    their blob.
    """

    def setUp(self):
        # A real MEDIA_ROOT: the clobber happens on disk, so a name-only
        # assertion passes against the buggy code.
        self._tmpdir = tempfile.mkdtemp()
        self._media_override = override_settings(MEDIA_ROOT=self._tmpdir)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

        self.bob = User.objects.create_user(username="bob", password="pw")
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.group = Group.objects.create(name="Design")
        self.bob.groups.add(self.group)
        self.alice.groups.add(self.group)
        self.group_root = FileService.create_folder(
            self.bob, "Design", group=self.group
        )

    def _file(self, owner, name, payload, parent=None):
        return FileService.create_file(
            owner, name, parent=parent, content=ContentFile(payload, name=name)
        )

    def test_copy_into_group_folder_does_not_clobber_a_teammate_blob(self):
        bobs = self._file(self.bob, "report.pdf", b"BOB-ORIGINAL", self.group_root)
        alices = self._file(self.alice, "report.pdf", b"ALICE-COPY")

        copied = FileService.copy(alices, self.group_root, self.alice)

        bobs.refresh_from_db()
        with bobs.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"BOB-ORIGINAL")
        self.assertNotEqual(copied.content.name, bobs.content.name)

    def test_a_copied_folder_does_not_merge_into_a_teammate_folder(self):
        bobs_folder = FileService.create_folder(
            self.bob, "specs", parent=self.group_root
        )
        bobs_child = self._file(self.bob, "spec.txt", b"BOB-ORIGINAL", bobs_folder)

        alices_folder = FileService.create_folder(self.alice, "specs")
        self._file(self.alice, "spec.txt", b"ALICE-COPY", alices_folder)

        copied = FileService.copy(alices_folder, self.group_root, self.alice)

        self.assertNotEqual(copied.pk, bobs_folder.pk)
        self.assertNotEqual(copied.name, bobs_folder.name)
        bobs_child.refresh_from_db()
        with bobs_child.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"BOB-ORIGINAL")
        self.assertEqual(
            File.objects.filter(parent=bobs_folder, deleted_at__isnull=True).count(), 1
        )
