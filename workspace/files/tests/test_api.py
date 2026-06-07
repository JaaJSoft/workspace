from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File

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
        File.objects.create(
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

    def test_patch_same_parent_is_noop(self):
        """PATCH with the file's existing parent must succeed without error.

        The serializer no longer short-circuits on parent equality before
        calling FileService.move() - move() handles the noop internally.
        Regression guard for that contract.
        """
        folder = File.objects.create(
            owner=self.user, name='Folder', node_type=File.NodeType.FOLDER,
        )
        file = File.objects.create(
            owner=self.user, name='file.txt',
            node_type=File.NodeType.FILE, parent=folder,
        )
        response = self.client.patch(f'/api/v1/files/{file.uuid}', {
            'parent': str(folder.uuid),
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        file.refresh_from_db()
        self.assertEqual(file.parent_id, folder.uuid)

    def test_patch_parent_and_content_together(self):
        """A single PATCH with both `parent` and `content` must apply both.

        Order matters in the serializer: move runs first (so the file's
        storage path resolves under the new parent), then content is
        written. Regression guard: a previous arrangement could leave the
        new bytes at the old storage path or skip the move entirely.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        src = File.objects.create(
            owner=self.user, name='Src', node_type=File.NodeType.FOLDER,
        )
        dest = File.objects.create(
            owner=self.user, name='Dest', node_type=File.NodeType.FOLDER,
        )
        file = File.objects.create(
            owner=self.user, name='note.txt',
            node_type=File.NodeType.FILE, parent=src,
        )
        file.content = ContentFile(b'old', name='note.txt')
        file.save()

        new_content = SimpleUploadedFile(
            'note.txt', b'new bytes here', content_type='text/plain',
        )
        response = self.client.patch(
            f'/api/v1/files/{file.uuid}',
            {'parent': str(dest.uuid), 'content': new_content},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        file.refresh_from_db()
        self.assertEqual(file.parent_id, dest.uuid)
        self.assertEqual(file.size, len(b'new bytes here'))
        # Read back to confirm the bytes actually live at the storage path
        # the row points at - not at the old src/ location.
        with file.content.storage.open(file.content.name, 'rb') as fh:
            self.assertEqual(fh.read(), b'new bytes here')

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

    def test_update_content_resets_has_thumbnail(self):
        """PATCH content must invalidate any cached thumbnail flag.

        Regression: before the FileSerializer was routed through
        FileService.update_content, the API path left has_thumbnail untouched,
        so an image whose bytes were replaced kept serving the stale thumbnail.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        file = File.objects.create(
            owner=self.user, name='photo.png', node_type=File.NodeType.FILE,
            has_thumbnail=True,
        )
        file.content = ContentFile(b'old', name='photo.png')
        file.save()

        new_content = SimpleUploadedFile('photo.png', b'new bytes', content_type='image/png')
        resp = self.client.patch(
            f'/api/v1/files/{file.uuid}',
            {'content': new_content},
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        file.refresh_from_db()
        self.assertFalse(file.has_thumbnail)

    def test_update_content_bumps_updated_at(self):
        """PATCH content must advance updated_at."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        file = File.objects.create(
            owner=self.user, name='doc.txt', node_type=File.NodeType.FILE,
        )
        file.content = ContentFile(b'old', name='doc.txt')
        file.save()
        original_updated_at = file.updated_at

        new_content = SimpleUploadedFile('doc.txt', b'new', content_type='text/plain')
        resp = self.client.patch(
            f'/api/v1/files/{file.uuid}',
            {'content': new_content},
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        file.refresh_from_db()
        self.assertGreater(file.updated_at, original_updated_at)

    def test_update_content_persists_new_size(self):
        """PATCH content must persist the new byte size on the row."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        file = File.objects.create(
            owner=self.user, name='doc.txt', node_type=File.NodeType.FILE,
            size=3,
        )
        file.content = ContentFile(b'old', name='doc.txt')
        file.save()

        new_content = SimpleUploadedFile(
            'doc.txt', b'much longer content', content_type='text/plain',
        )
        resp = self.client.patch(
            f'/api/v1/files/{file.uuid}',
            {'content': new_content},
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        file.refresh_from_db()
        self.assertEqual(file.size, len(b'much longer content'))

    def test_list_ordering_case_insensitive(self):
        """Name sort must be case-insensitive (a < B < c)."""
        for name in ['banana', 'Cherry', 'apple']:
            File.objects.create(
                owner=self.user,
                name=name,
                node_type=File.NodeType.FILE,
            )

        response = self.client.get('/api/v1/files')
        names = [f['name'] for f in response.data]
        self.assertEqual(names, ['apple', 'banana', 'Cherry'])

    def test_list_ordering_by_name_param_case_insensitive(self):
        """Explicit ?ordering=name must also be case-insensitive."""
        for name in ['banana', 'Cherry', 'apple']:
            File.objects.create(
                owner=self.user,
                name=name,
                node_type=File.NodeType.FILE,
            )

        response = self.client.get('/api/v1/files?ordering=name')
        names = [f['name'] for f in response.data]
        self.assertEqual(names, ['apple', 'banana', 'Cherry'])

    def test_list_ordering_by_name_desc_case_insensitive(self):
        """Explicit ?ordering=-name must also be case-insensitive."""
        for name in ['banana', 'Cherry', 'apple']:
            File.objects.create(
                owner=self.user,
                name=name,
                node_type=File.NodeType.FILE,
            )

        response = self.client.get('/api/v1/files?ordering=-name')
        names = [f['name'] for f in response.data]
        self.assertEqual(names, ['Cherry', 'banana', 'apple'])


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
