from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from rest_framework.test import APITestCase
from rest_framework import status

from workspace.files.models import File, FileFavorite, PinnedFolder

User = get_user_model()


class FileModelTests(TestCase):
    """Tests for the File model."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

    def test_create_folder(self):
        """Test creating a folder."""
        folder = File.objects.create(
            owner=self.user,
            name='Documents',
            node_type=File.NodeType.FOLDER
        )
        self.assertEqual(folder.name, 'Documents')
        self.assertEqual(folder.node_type, File.NodeType.FOLDER)
        self.assertEqual(folder.path, 'Documents')
        self.assertIsNone(folder.parent)

    def test_create_nested_folder(self):
        """Test creating nested folders."""
        parent = File.objects.create(
            owner=self.user,
            name='Documents',
            node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user,
            name='Work',
            node_type=File.NodeType.FOLDER,
            parent=parent
        )
        self.assertEqual(child.path, 'Documents/Work')
        self.assertEqual(child.parent, parent)

    def test_create_file(self):
        """Test creating a file."""
        file = File.objects.create(
            owner=self.user,
            name='test.txt',
            node_type=File.NodeType.FILE,
            mime_type='text/plain'
        )
        self.assertEqual(file.name, 'test.txt')
        self.assertEqual(file.node_type, File.NodeType.FILE)

    def test_create_file_with_content(self):
        """Test creating a file with content."""
        file = File(
            owner=self.user,
            name='test.txt',
            node_type=File.NodeType.FILE,
            mime_type='text/plain'
        )
        file.content = ContentFile(b'Hello World', name='test.txt')
        file.size = 11
        file.save()

        self.assertEqual(file.size, 11)
        self.assertTrue(file.content)

    def test_path_updates_on_rename(self):
        """Test that path updates when a folder is renamed."""
        folder = File.objects.create(
            owner=self.user,
            name='Old',
            node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user,
            name='Child',
            node_type=File.NodeType.FOLDER,
            parent=folder
        )

        folder.name = 'New'
        folder.save()

        child.refresh_from_db()
        self.assertEqual(folder.path, 'New')
        self.assertEqual(child.path, 'New/Child')

    def test_path_updates_on_move(self):
        """Test that path updates when a folder is moved."""
        folder1 = File.objects.create(
            owner=self.user,
            name='Folder1',
            node_type=File.NodeType.FOLDER
        )
        folder2 = File.objects.create(
            owner=self.user,
            name='Folder2',
            node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user,
            name='Child',
            node_type=File.NodeType.FOLDER,
            parent=folder1
        )

        self.assertEqual(child.path, 'Folder1/Child')

        child.parent = folder2
        child.save()

        child.refresh_from_db()
        self.assertEqual(child.path, 'Folder2/Child')

    def test_soft_delete(self):
        """Test soft delete."""
        folder = File.objects.create(
            owner=self.user,
            name='ToDelete',
            node_type=File.NodeType.FOLDER
        )
        folder.delete()

        folder.refresh_from_db()
        self.assertIsNotNone(folder.deleted_at)
        self.assertTrue(folder.is_deleted())

    def test_soft_delete_cascades_to_children(self):
        """Test that soft delete cascades to children."""
        folder = File.objects.create(
            owner=self.user,
            name='Parent',
            node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user,
            name='Child',
            node_type=File.NodeType.FILE,
            parent=folder
        )

        folder.delete()

        folder.refresh_from_db()
        child.refresh_from_db()
        self.assertIsNotNone(folder.deleted_at)
        self.assertIsNotNone(child.deleted_at)

    def test_restore(self):
        """Test restoring a deleted item."""
        folder = File.objects.create(
            owner=self.user,
            name='ToRestore',
            node_type=File.NodeType.FOLDER
        )
        folder.delete()
        folder.refresh_from_db()
        self.assertIsNotNone(folder.deleted_at)

        folder.restore()
        folder.refresh_from_db()
        self.assertIsNone(folder.deleted_at)

    def test_restore_cascades_to_children(self):
        """Test that restore cascades to children."""
        folder = File.objects.create(
            owner=self.user,
            name='Parent',
            node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user,
            name='Child',
            node_type=File.NodeType.FILE,
            parent=folder
        )

        folder.delete()
        folder.refresh_from_db()

        folder.restore()
        folder.refresh_from_db()
        child.refresh_from_db()

        self.assertIsNone(folder.deleted_at)
        self.assertIsNone(child.deleted_at)

    def test_hard_delete(self):
        """Test hard delete permanently removes the item."""
        folder = File.objects.create(
            owner=self.user,
            name='ToDelete',
            node_type=File.NodeType.FOLDER
        )
        uuid = folder.uuid
        folder.delete(hard=True)

        self.assertFalse(File.objects.filter(uuid=uuid).exists())


class FileAPITests(APITestCase):
    """Tests for the File API endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.other_user = User.objects.create_user(
            username='otheruser',
            email='other@example.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

    def test_list_files_empty(self):
        """Test listing files when none exist."""
        response = self.client.get('/api/v1/files')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_create_folder(self):
        """Test creating a folder via API."""
        response = self.client.post('/api/v1/files', {
            'name': 'Documents',
            'node_type': 'folder'
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['name'], 'Documents')
        self.assertEqual(response.data['node_type'], 'folder')

    def test_create_nested_folder(self):
        """Test creating a nested folder via API."""
        parent = File.objects.create(
            owner=self.user,
            name='Documents',
            node_type=File.NodeType.FOLDER
        )
        response = self.client.post('/api/v1/files', {
            'name': 'Work',
            'node_type': 'folder',
            'parent': str(parent.uuid)
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(response.data['parent']), str(parent.uuid))

    def test_list_only_root_files(self):
        """Test that listing without parent filter returns only root items."""
        root = File.objects.create(
            owner=self.user,
            name='Root',
            node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user,
            name='Child',
            node_type=File.NodeType.FOLDER,
            parent=root
        )

        response = self.client.get('/api/v1/files')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['uuid'], str(root.uuid))

    def test_list_folder_contents(self):
        """Test listing contents of a folder."""
        root = File.objects.create(
            owner=self.user,
            name='Root',
            node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user,
            name='Child',
            node_type=File.NodeType.FILE,
            parent=root
        )

        response = self.client.get(f'/api/v1/files?parent={root.uuid}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['uuid'], str(child.uuid))

    def test_retrieve_file(self):
        """Test retrieving a specific file."""
        file = File.objects.create(
            owner=self.user,
            name='test.txt',
            node_type=File.NodeType.FILE
        )
        response = self.client.get(f'/api/v1/files/{file.uuid}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'test.txt')

    def test_update_file_name(self):
        """Test renaming a file."""
        file = File.objects.create(
            owner=self.user,
            name='old.txt',
            node_type=File.NodeType.FILE
        )
        response = self.client.patch(f'/api/v1/files/{file.uuid}', {
            'name': 'new.txt'
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'new.txt')

    def test_move_file(self):
        """Test moving a file to another folder."""
        folder1 = File.objects.create(
            owner=self.user,
            name='Folder1',
            node_type=File.NodeType.FOLDER
        )
        folder2 = File.objects.create(
            owner=self.user,
            name='Folder2',
            node_type=File.NodeType.FOLDER
        )
        file = File.objects.create(
            owner=self.user,
            name='file.txt',
            node_type=File.NodeType.FILE,
            parent=folder1
        )

        response = self.client.patch(f'/api/v1/files/{file.uuid}', {
            'parent': str(folder2.uuid)
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(str(response.data['parent']), str(folder2.uuid))

        file.refresh_from_db()
        self.assertEqual(file.path, 'Folder2/file.txt')

    def test_move_file_to_root(self):
        """Test moving a file to root (parent=null)."""
        folder = File.objects.create(
            owner=self.user,
            name='Folder',
            node_type=File.NodeType.FOLDER
        )
        file = File.objects.create(
            owner=self.user,
            name='file.txt',
            node_type=File.NodeType.FILE,
            parent=folder
        )

        response = self.client.patch(
            f'/api/v1/files/{file.uuid}',
            {'parent': None},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['parent'])

        file.refresh_from_db()
        self.assertEqual(file.path, 'file.txt')

    def test_delete_file(self):
        """Test deleting a file (soft delete)."""
        file = File.objects.create(
            owner=self.user,
            name='todelete.txt',
            node_type=File.NodeType.FILE
        )
        response = self.client.delete(f'/api/v1/files/{file.uuid}')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        file.refresh_from_db()
        self.assertIsNotNone(file.deleted_at)

    def test_cannot_access_other_user_files(self):
        """Test that users cannot access other users' files."""
        other_file = File.objects.create(
            owner=self.other_user,
            name='private.txt',
            node_type=File.NodeType.FILE
        )
        response = self.client.get(f'/api/v1/files/{other_file.uuid}')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_move_to_other_user_folder(self):
        """Test that users cannot move files to other users' folders."""
        other_folder = File.objects.create(
            owner=self.other_user,
            name='OtherFolder',
            node_type=File.NodeType.FOLDER
        )
        my_file = File.objects.create(
            owner=self.user,
            name='myfile.txt',
            node_type=File.NodeType.FILE
        )

        response = self.client.patch(f'/api/v1/files/{my_file.uuid}', {
            'parent': str(other_folder.uuid)
        })
        # Should fail validation - parent folder not found
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


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
        child1 = File.objects.create(
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
        grandchild = File.objects.create(
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


class FavoriteAPITests(APITestCase):
    """Tests for the favorites functionality."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

    def test_add_favorite(self):
        """Test adding a file to favorites."""
        file = File.objects.create(
            owner=self.user,
            name='favme.txt',
            node_type=File.NodeType.FILE
        )

        response = self.client.post(f'/api/v1/files/{file.uuid}/favorite')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_favorite'])

        self.assertTrue(FileFavorite.objects.filter(
            owner=self.user,
            file=file
        ).exists())

    def test_remove_favorite(self):
        """Test removing a file from favorites."""
        file = File.objects.create(
            owner=self.user,
            name='unfavme.txt',
            node_type=File.NodeType.FILE
        )
        FileFavorite.objects.create(owner=self.user, file=file)

        response = self.client.delete(f'/api/v1/files/{file.uuid}/favorite')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['is_favorite'])

        self.assertFalse(FileFavorite.objects.filter(
            owner=self.user,
            file=file
        ).exists())

    def test_list_favorites(self):
        """Test listing favorite files."""
        file1 = File.objects.create(
            owner=self.user,
            name='fav1.txt',
            node_type=File.NodeType.FILE
        )
        file2 = File.objects.create(
            owner=self.user,
            name='notfav.txt',
            node_type=File.NodeType.FILE
        )
        FileFavorite.objects.create(owner=self.user, file=file1)

        response = self.client.get('/api/v1/files?favorites=1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['uuid'], str(file1.uuid))


class PinnedFolderAPITests(APITestCase):
    """Tests for the pinned folders functionality."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

    def test_pin_folder(self):
        """Test pinning a folder."""
        folder = File.objects.create(
            owner=self.user,
            name='PinMe',
            node_type=File.NodeType.FOLDER
        )

        response = self.client.post(f'/api/v1/files/{folder.uuid}/pin')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_pinned'])

        self.assertTrue(PinnedFolder.objects.filter(
            owner=self.user,
            folder=folder
        ).exists())

    def test_unpin_folder(self):
        """Test unpinning a folder."""
        folder = File.objects.create(
            owner=self.user,
            name='UnpinMe',
            node_type=File.NodeType.FOLDER
        )
        PinnedFolder.objects.create(owner=self.user, folder=folder)

        response = self.client.delete(f'/api/v1/files/{folder.uuid}/pin')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['is_pinned'])

    def test_cannot_pin_file(self):
        """Test that files cannot be pinned."""
        file = File.objects.create(
            owner=self.user,
            name='notafolder.txt',
            node_type=File.NodeType.FILE
        )

        response = self.client.post(f'/api/v1/files/{file.uuid}/pin')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_pinned_folders(self):
        """Test listing pinned folders."""
        folder1 = File.objects.create(
            owner=self.user,
            name='Pinned1',
            node_type=File.NodeType.FOLDER
        )
        folder2 = File.objects.create(
            owner=self.user,
            name='NotPinned',
            node_type=File.NodeType.FOLDER
        )
        PinnedFolder.objects.create(owner=self.user, folder=folder1, position=0)

        response = self.client.get('/api/v1/files/pinned')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['uuid'], str(folder1.uuid))

    def test_reorder_pinned_folders(self):
        """Test reordering pinned folders."""
        folder1 = File.objects.create(
            owner=self.user,
            name='First',
            node_type=File.NodeType.FOLDER
        )
        folder2 = File.objects.create(
            owner=self.user,
            name='Second',
            node_type=File.NodeType.FOLDER
        )
        PinnedFolder.objects.create(owner=self.user, folder=folder1, position=0)
        PinnedFolder.objects.create(owner=self.user, folder=folder2, position=1)

        # Reverse order
        response = self.client.post('/api/v1/files/pinned/reorder', {
            'order': [str(folder2.uuid), str(folder1.uuid)]
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify new order
        pins = PinnedFolder.objects.filter(owner=self.user).order_by('position')
        self.assertEqual(pins[0].folder, folder2)
        self.assertEqual(pins[1].folder, folder1)


class TrashAPITests(APITestCase):
    """Tests for the trash functionality."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

    def test_list_trash(self):
        """Test listing trashed items."""
        file = File.objects.create(
            owner=self.user,
            name='trashed.txt',
            node_type=File.NodeType.FILE
        )
        file.delete()  # Soft delete

        response = self.client.get('/api/v1/files/trash')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['uuid'], str(file.uuid))

    def test_trash_does_not_show_active_files(self):
        """Test that trash only shows deleted items."""
        active_file = File.objects.create(
            owner=self.user,
            name='active.txt',
            node_type=File.NodeType.FILE
        )
        trashed_file = File.objects.create(
            owner=self.user,
            name='trashed.txt',
            node_type=File.NodeType.FILE
        )
        trashed_file.delete()

        response = self.client.get('/api/v1/files/trash')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['uuid'], str(trashed_file.uuid))

    def test_restore_from_trash(self):
        """Test restoring an item from trash."""
        file = File.objects.create(
            owner=self.user,
            name='torestore.txt',
            node_type=File.NodeType.FILE
        )
        file.delete()
        file.refresh_from_db()
        self.assertIsNotNone(file.deleted_at)

        response = self.client.post(f'/api/v1/files/{file.uuid}/restore')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        file.refresh_from_db()
        self.assertIsNone(file.deleted_at)

    def test_purge_from_trash(self):
        """Test permanently deleting an item from trash."""
        file = File.objects.create(
            owner=self.user,
            name='topurge.txt',
            node_type=File.NodeType.FILE
        )
        file.delete()
        uuid = file.uuid

        response = self.client.delete(f'/api/v1/files/{uuid}/purge')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.assertFalse(File.objects.filter(uuid=uuid).exists())

    def test_cannot_purge_active_file(self):
        """Test that active files cannot be purged."""
        file = File.objects.create(
            owner=self.user,
            name='active.txt',
            node_type=File.NodeType.FILE
        )

        response = self.client.delete(f'/api/v1/files/{file.uuid}/purge')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_clean_trash(self):
        """Test cleaning trash with force option."""
        file = File.objects.create(
            owner=self.user,
            name='trashed.txt',
            node_type=File.NodeType.FILE
        )
        file.delete()

        response = self.client.delete('/api/v1/files/trash/clean?force=1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['deleted'], 1)

        self.assertFalse(File.objects.filter(uuid=file.uuid).exists())


class EdgeCaseTests(APITestCase):
    """Tests for edge cases and error handling."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

    def test_move_folder_into_itself_fails(self):
        """Test that moving a folder into itself fails."""
        folder = File.objects.create(
            owner=self.user,
            name='Folder',
            node_type=File.NodeType.FOLDER
        )

        response = self.client.patch(f'/api/v1/files/{folder.uuid}', {
            'parent': str(folder.uuid)
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_move_folder_into_descendant_fails(self):
        """Test that moving a folder into its descendant fails."""
        parent = File.objects.create(
            owner=self.user,
            name='Parent',
            node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user,
            name='Child',
            node_type=File.NodeType.FOLDER,
            parent=parent
        )

        response = self.client.patch(f'/api/v1/files/{parent.uuid}', {
            'parent': str(child.uuid)
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_deep_nesting_path_updates(self):
        """Test that path updates correctly for deeply nested structures."""
        level1 = File.objects.create(
            owner=self.user,
            name='L1',
            node_type=File.NodeType.FOLDER
        )
        level2 = File.objects.create(
            owner=self.user,
            name='L2',
            node_type=File.NodeType.FOLDER,
            parent=level1
        )
        level3 = File.objects.create(
            owner=self.user,
            name='L3',
            node_type=File.NodeType.FOLDER,
            parent=level2
        )
        file = File.objects.create(
            owner=self.user,
            name='deep.txt',
            node_type=File.NodeType.FILE,
            parent=level3
        )

        self.assertEqual(file.path, 'L1/L2/L3/deep.txt')

        # Rename L1
        level1.name = 'Level1'
        level1.save()

        file.refresh_from_db()
        self.assertEqual(file.path, 'Level1/L2/L3/deep.txt')

    def test_special_characters_in_name(self):
        """Test handling of special characters in file names."""
        file = File.objects.create(
            owner=self.user,
            name='file (1) [test] {data}.txt',
            node_type=File.NodeType.FILE
        )
        self.assertEqual(file.name, 'file (1) [test] {data}.txt')

        response = self.client.get(f'/api/v1/files/{file.uuid}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'file (1) [test] {data}.txt')

    def test_unicode_in_name(self):
        """Test handling of unicode characters in file names."""
        file = File.objects.create(
            owner=self.user,
            name='文档.txt',
            node_type=File.NodeType.FILE
        )
        self.assertEqual(file.name, '文档.txt')

        response = self.client.get(f'/api/v1/files/{file.uuid}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], '文档.txt')

    def test_deleted_parent_children_behavior(self):
        """Test behavior when parent is deleted but child is restored."""
        parent = File.objects.create(
            owner=self.user,
            name='Parent',
            node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user,
            name='Child',
            node_type=File.NodeType.FILE,
            parent=parent
        )

        # Delete parent (should cascade to child)
        parent.delete()
        parent.refresh_from_db()
        child.refresh_from_db()

        self.assertIsNotNone(parent.deleted_at)
        self.assertIsNotNone(child.deleted_at)

        # Restore child - should also restore parent
        child.restore()
        parent.refresh_from_db()
        child.refresh_from_db()

        self.assertIsNone(child.deleted_at)
        # Parent should also be restored
        self.assertIsNone(parent.deleted_at)
