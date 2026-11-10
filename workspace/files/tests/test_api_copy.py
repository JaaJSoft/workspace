from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.tests.operations import outcome_of, run_operation

User = get_user_model()


class CopyAPITests(APITestCase):
    """Copies through the batch operations API."""

    def copy(self, node, target=None):
        return run_operation(self.client, "copy", [node], target)

    def copy_of(self, node, parent=None):
        """The one row the copy of *node* left under *parent*."""
        return File.objects.exclude(pk=node.pk).get(
            parent=parent,
            node_type=node.node_type,
            name__startswith=node.name.split(".")[0],
        )

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        self.client.force_authenticate(user=self.user)

    def test_copy_file(self):
        """Test copying a file."""
        file = File.objects.create(
            owner=self.user,
            name="original.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )

        response = self.copy(file)
        self.assertEqual(outcome_of(response), ({"done": 1}, []))
        self.assertIn("Copy", self.copy_of(file).name)

        # Verify original still exists
        file.refresh_from_db()
        self.assertEqual(file.name, "original.txt")

    def test_copy_file_to_folder(self):
        """Test copying a file to a specific folder."""
        folder = File.objects.create(
            owner=self.user, name="Target", node_type=File.NodeType.FOLDER
        )
        file = File.objects.create(
            owner=self.user, name="file.txt", node_type=File.NodeType.FILE
        )

        response = self.copy(file, folder)
        self.assertEqual(outcome_of(response), ({"done": 1}, []))
        self.assertEqual(self.copy_of(file, folder).name, "file.txt")

    def test_copy_file_with_content(self):
        """Test copying a file preserves content."""
        file = File(
            owner=self.user,
            name="withcontent.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        file.content = ContentFile(b"Test content", name="withcontent.txt")
        file.size = 12
        file.save()

        self.copy(file)

        # Verify copy has content
        copy = self.copy_of(file)
        self.assertEqual(copy.size, 12)
        self.assertTrue(copy.content)

    def test_destination_save_failure_does_not_log_source_missing(self):
        """When the destination storage fails (e.g. disk full, perm denied
        on the destination path, remote storage flake), the warning
        'Source blob missing' must NOT be emitted: the source is fine, the
        issue is on the destination side. The try/except around copy_node's
        source-open step must be narrow enough that destination-side
        OSErrors propagate without misattributed logging.

        Calls the service directly rather than the API: the OSError aborts
        the request transaction, which causes a TransactionManagementError
        cascade through the test client. The service-level call exercises
        the same copy_node code path without that infrastructure noise.
        """
        from workspace.files.services.files import FileService

        file = File(
            owner=self.user,
            name="legit-source.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        file.content = ContentFile(b"src-data", name="legit-source.txt")
        file.size = 8
        file.save()

        with (
            patch(
                "workspace.files.services.storage_ops.logger.warning",
            ) as mock_warn,
            patch.object(
                File._meta.get_field("content").storage,
                "_save",
                side_effect=OSError("disk full"),
            ),
        ):
            with self.assertRaises(OSError):
                FileService.copy(file, None, self.user)

        misattributed = [
            call
            for call in mock_warn.call_args_list
            if call.args and "Source blob missing" in call.args[0]
        ]
        self.assertEqual(misattributed, [])

    def test_copy_file_creates_independent_blob(self):
        """The copied file's storage path must differ from the source's, and
        the bytes must round-trip. Pins down the FieldFile-vs-File _committed
        pitfall: if the source FieldFile were ever passed straight through to
        the destination FileField, Django would skip storage.save() and the
        two rows would point at the same blob - delete the source later and
        the copy goes blank.
        """
        file = File(
            owner=self.user,
            name="source.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        file.content = ContentFile(b"payload-bytes", name="source.txt")
        file.size = 13
        file.save()

        self.copy(file)

        copy = self.copy_of(file)
        self.assertNotEqual(copy.content.name, file.content.name)
        with copy.content.open("rb") as f:
            self.assertEqual(f.read(), b"payload-bytes")

    def test_copy_folder_empty(self):
        """Test copying an empty folder."""
        folder = File.objects.create(
            owner=self.user, name="EmptyFolder", node_type=File.NodeType.FOLDER
        )

        response = self.copy(folder)
        self.assertEqual(outcome_of(response), ({"done": 1}, []))
        self.assertIn("Copy", self.copy_of(folder).name)

    def test_copy_folder_with_children(self):
        """Test copying a folder with children copies recursively."""
        folder = File.objects.create(
            owner=self.user, name="Parent", node_type=File.NodeType.FOLDER
        )
        File.objects.create(
            owner=self.user,
            name="child1.txt",
            node_type=File.NodeType.FILE,
            parent=folder,
        )
        child2 = File.objects.create(
            owner=self.user,
            name="Subfolder",
            node_type=File.NodeType.FOLDER,
            parent=folder,
        )
        File.objects.create(
            owner=self.user,
            name="grandchild.txt",
            node_type=File.NodeType.FILE,
            parent=child2,
        )

        self.copy(folder)

        # Get the copied folder
        copied_folder = self.copy_of(folder)

        # Verify children were copied
        copied_children = File.objects.filter(parent=copied_folder)
        self.assertEqual(copied_children.count(), 2)

        # Verify grandchild was copied
        copied_subfolder = copied_children.get(node_type=File.NodeType.FOLDER)
        copied_grandchildren = File.objects.filter(parent=copied_subfolder)
        self.assertEqual(copied_grandchildren.count(), 1)

    def test_copy_name_conflict(self):
        """Test that copying handles name conflicts."""
        file = File.objects.create(
            owner=self.user, name="conflict.txt", node_type=File.NodeType.FILE
        )
        # Create a file that would conflict
        File.objects.create(
            owner=self.user, name="conflict (Copy).txt", node_type=File.NodeType.FILE
        )

        self.copy(file)
        # Should have "Copy 2" in name
        self.assertTrue(File.objects.filter(name="conflict (Copy 2).txt").exists())

    def test_cannot_copy_folder_into_itself(self):
        """Test that copying a folder into itself fails."""
        folder = File.objects.create(
            owner=self.user, name="Folder", node_type=File.NodeType.FOLDER
        )

        response = self.copy(folder, folder)
        self.assertEqual(
            outcome_of(response), ({"failed": 1}, [("Folder", "into_itself")])
        )

    def test_cannot_copy_folder_into_descendant(self):
        """Test that copying a folder into its descendant fails."""
        folder = File.objects.create(
            owner=self.user, name="Parent", node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user, name="Child", node_type=File.NodeType.FOLDER, parent=folder
        )

        response = self.copy(folder, child)
        self.assertEqual(
            outcome_of(response), ({"failed": 1}, [("Parent", "into_itself")])
        )

    def test_copy_into_group_subfolder_owned_by_another_member(self):
        """Regression: a group member must be able to copy into a folder
        owned by another member of the same group. The previous code
        hard-coded ``owner=request.user`` on the parent lookup, which
        rejected this legitimate case with 'Parent folder not found.'"""
        group = Group.objects.create(name="Marketing")
        alice = User.objects.create_user(username="alice", password="pw")
        alice.groups.add(group)
        self.user.groups.add(group)

        # alice creates the group root and a subfolder.
        group_root = File.objects.create(
            owner=alice,
            name="Marketing",
            node_type=File.NodeType.FOLDER,
            group=group,
        )
        group_sub = File.objects.create(
            owner=alice,
            name="Reports",
            node_type=File.NodeType.FOLDER,
            group=group,
            parent=group_root,
        )
        # testuser (group member, not owner of the folder) copies a file into it.
        my_file = File.objects.create(
            owner=self.user,
            name="draft.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )

        response = self.copy(my_file, group_sub)
        self.assertEqual(outcome_of(response), ({"done": 1}, []))
        self.assertTrue(
            File.objects.filter(parent=group_sub, name="draft.txt").exists()
        )

    def test_copy_with_malformed_parent_uuid_returns_400(self):
        """Garbage parent UUID returns 400 via parse_uuid_or_none, not 500."""
        my_file = File.objects.create(
            owner=self.user,
            name="mine.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )

        response = self.copy(my_file, "not-a-uuid")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_copy_into_unrelated_user_folder_still_400(self):
        """Negative control: someone else's personal folder is still
        rejected with 400, even after access widening."""
        other = User.objects.create_user(username="stranger", password="pw")
        their_folder = File.objects.create(
            owner=other,
            name="Theirs",
            node_type=File.NodeType.FOLDER,
        )
        my_file = File.objects.create(
            owner=self.user,
            name="mine.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )

        response = self.copy(my_file, their_folder)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(File.objects.filter(parent=their_folder).exists())

    def test_copy_preserves_icon_and_color(self):
        """Test that copying preserves folder icon and color."""
        folder = File.objects.create(
            owner=self.user,
            name="Styled",
            node_type=File.NodeType.FOLDER,
            icon="briefcase",
            color="text-error",
        )

        self.copy(folder)
        copy = self.copy_of(folder)
        self.assertEqual(copy.icon, "briefcase")
        self.assertEqual(copy.color, "text-error")
