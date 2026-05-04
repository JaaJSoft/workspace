from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.storage import OverwriteStorage

User = get_user_model()


class CopyAPITests(APITestCase):
    """Tests for the copy endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

    def test_copy_file(self):
        """Test copying a file."""
        file = File.objects.create(
            owner=self.user,
            name='original.txt',
            node_type=File.NodeType.FILE,
            mime_type='text/plain'
        )

        response = self.client.post(
            f'/api/v1/files/{file.uuid}/copy',
            {'parent': None},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('Copy', response.data['name'])

        # Verify original still exists
        file.refresh_from_db()
        self.assertEqual(file.name, 'original.txt')

    def test_copy_file_to_folder(self):
        """Test copying a file to a specific folder."""
        folder = File.objects.create(
            owner=self.user,
            name='Target',
            node_type=File.NodeType.FOLDER
        )
        file = File.objects.create(
            owner=self.user,
            name='file.txt',
            node_type=File.NodeType.FILE
        )

        response = self.client.post(
            f'/api/v1/files/{file.uuid}/copy',
            {'parent': str(folder.uuid)},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(response.data['parent']), str(folder.uuid))

    def test_copy_file_with_content(self):
        """Test copying a file preserves content."""
        file = File(
            owner=self.user,
            name='withcontent.txt',
            node_type=File.NodeType.FILE,
            mime_type='text/plain'
        )
        file.content = ContentFile(b'Test content', name='withcontent.txt')
        file.size = 12
        file.save()

        response = self.client.post(
            f'/api/v1/files/{file.uuid}/copy',
            {'parent': None},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Verify copy has content
        copy = File.objects.get(uuid=response.data['uuid'])
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
            name='legit-source.txt',
            node_type=File.NodeType.FILE,
            mime_type='text/plain',
        )
        file.content = ContentFile(b'src-data', name='legit-source.txt')
        file.size = 8
        file.save()

        with patch(
            'workspace.files.services._storage_ops.logger.warning',
        ) as mock_warn, patch.object(
            OverwriteStorage, '_save', side_effect=OSError('disk full'),
        ):
            with self.assertRaises(OSError):
                FileService.copy(file, None, self.user)

        misattributed = [
            call for call in mock_warn.call_args_list
            if call.args and 'Source blob missing' in call.args[0]
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
            name='source.txt',
            node_type=File.NodeType.FILE,
            mime_type='text/plain',
        )
        file.content = ContentFile(b'payload-bytes', name='source.txt')
        file.size = 13
        file.save()

        response = self.client.post(
            f'/api/v1/files/{file.uuid}/copy', {'parent': None}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        copy = File.objects.get(uuid=response.data['uuid'])
        self.assertNotEqual(copy.content.name, file.content.name)
        with copy.content.open('rb') as f:
            self.assertEqual(f.read(), b'payload-bytes')

    def test_copy_folder_empty(self):
        """Test copying an empty folder."""
        folder = File.objects.create(
            owner=self.user,
            name='EmptyFolder',
            node_type=File.NodeType.FOLDER
        )

        response = self.client.post(
            f'/api/v1/files/{folder.uuid}/copy',
            {'parent': None},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('Copy', response.data['name'])

    def test_copy_folder_with_children(self):
        """Test copying a folder with children copies recursively."""
        folder = File.objects.create(
            owner=self.user,
            name='Parent',
            node_type=File.NodeType.FOLDER
        )
        File.objects.create(
            owner=self.user,
            name='child1.txt',
            node_type=File.NodeType.FILE,
            parent=folder
        )
        child2 = File.objects.create(
            owner=self.user,
            name='Subfolder',
            node_type=File.NodeType.FOLDER,
            parent=folder
        )
        File.objects.create(
            owner=self.user,
            name='grandchild.txt',
            node_type=File.NodeType.FILE,
            parent=child2
        )

        response = self.client.post(
            f'/api/v1/files/{folder.uuid}/copy',
            {'parent': None},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Get the copied folder
        copied_folder = File.objects.get(uuid=response.data['uuid'])

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
            owner=self.user,
            name='conflict.txt',
            node_type=File.NodeType.FILE
        )
        # Create a file that would conflict
        File.objects.create(
            owner=self.user,
            name='conflict (Copy).txt',
            node_type=File.NodeType.FILE
        )

        response = self.client.post(
            f'/api/v1/files/{file.uuid}/copy',
            {'parent': None},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # Should have "Copy 2" in name
        self.assertIn('Copy 2', response.data['name'])

    def test_cannot_copy_folder_into_itself(self):
        """Test that copying a folder into itself fails."""
        folder = File.objects.create(
            owner=self.user,
            name='Folder',
            node_type=File.NodeType.FOLDER
        )

        response = self.client.post(f'/api/v1/files/{folder.uuid}/copy', {
            'parent': str(folder.uuid)
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_copy_folder_into_descendant(self):
        """Test that copying a folder into its descendant fails."""
        folder = File.objects.create(
            owner=self.user,
            name='Parent',
            node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user,
            name='Child',
            node_type=File.NodeType.FOLDER,
            parent=folder
        )

        response = self.client.post(f'/api/v1/files/{folder.uuid}/copy', {
            'parent': str(child.uuid)
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_copy_into_group_subfolder_owned_by_another_member(self):
        """Regression: a group member must be able to copy into a folder
        owned by another member of the same group. The previous code
        hard-coded ``owner=request.user`` on the parent lookup, which
        rejected this legitimate case with 'Parent folder not found.'"""
        group = Group.objects.create(name='Marketing')
        alice = User.objects.create_user(username='alice', password='pw')
        alice.groups.add(group)
        self.user.groups.add(group)

        # alice creates the group root and a subfolder.
        group_root = File.objects.create(
            owner=alice, name='Marketing', node_type=File.NodeType.FOLDER, group=group,
        )
        group_sub = File.objects.create(
            owner=alice, name='Reports', node_type=File.NodeType.FOLDER,
            group=group, parent=group_root,
        )
        # testuser (group member, not owner of the folder) copies a file into it.
        my_file = File.objects.create(
            owner=self.user, name='draft.txt', node_type=File.NodeType.FILE,
            mime_type='text/plain',
        )

        response = self.client.post(
            f'/api/v1/files/{my_file.uuid}/copy',
            {'parent': str(group_sub.uuid)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_copy_into_unrelated_user_folder_still_400(self):
        """Negative control: someone else's personal folder is still
        rejected with 400, even after access widening."""
        other = User.objects.create_user(username='stranger', password='pw')
        their_folder = File.objects.create(
            owner=other, name='Theirs', node_type=File.NodeType.FOLDER,
        )
        my_file = File.objects.create(
            owner=self.user, name='mine.txt', node_type=File.NodeType.FILE,
            mime_type='text/plain',
        )

        response = self.client.post(
            f'/api/v1/files/{my_file.uuid}/copy',
            {'parent': str(their_folder.uuid)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_copy_preserves_icon_and_color(self):
        """Test that copying preserves folder icon and color."""
        folder = File.objects.create(
            owner=self.user,
            name='Styled',
            node_type=File.NodeType.FOLDER,
            icon='briefcase',
            color='text-error'
        )

        response = self.client.post(f'/api/v1/files/{folder.uuid}/copy', {
            'parent': None
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['icon'], 'briefcase')
        self.assertEqual(response.data['color'], 'text-error')
