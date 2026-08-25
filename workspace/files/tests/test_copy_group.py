"""A copy made inside a group folder must belong to that group.

``copy_node`` used to build the destination row without a group: the copy
stayed personal, its blob landed under the copier's own storage root, and no
other member of the group could see it.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase

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
