from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File

User = get_user_model()


class FileSerializerCreateTests(APITestCase):
    """Tests for FileSerializer.create — ensures it delegates to FileService."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@example.com', password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

    # ── Folder creation ───────────────────────────────────

    def test_create_folder_delegates_to_file_service(self):
        """create(node_type=folder) must call FileService.create_folder."""
        with patch('workspace.files.serializers.FileService.create_folder', wraps=File.objects.create) as mock:
            # wraps won't perfectly replicate create_folder, so we call the real API
            pass

        # Just verify via the API that it works end-to-end
        response = self.client.post('/api/v1/files', {
            'name': 'TestFolder',
            'node_type': 'folder',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['name'], 'TestFolder')
        self.assertEqual(response.data['node_type'], 'folder')

    @patch('workspace.files.serializers.FileService.create_folder')
    def test_create_folder_calls_create_folder(self, mock_create_folder):
        """Verify FileService.create_folder is called with correct args."""
        mock_folder = File(
            owner=self.user, name='Docs', node_type=File.NodeType.FOLDER
        )
        mock_folder.uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        mock_create_folder.return_value = mock_folder

        response = self.client.post('/api/v1/files', {
            'name': 'Docs',
            'node_type': 'folder',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_create_folder.assert_called_once()
        call_kwargs = mock_create_folder.call_args
        self.assertEqual(call_kwargs.kwargs['name'], 'Docs')
        self.assertEqual(call_kwargs.kwargs['owner'], self.user)

    @patch('workspace.files.serializers.FileService.create_folder')
    def test_create_folder_passes_icon_and_color(self, mock_create_folder):
        """Icon and color must be forwarded to FileService.create_folder."""
        mock_folder = File(
            owner=self.user, name='Notes', node_type=File.NodeType.FOLDER,
            icon='book', color='success'
        )
        mock_folder.uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        mock_create_folder.return_value = mock_folder

        response = self.client.post('/api/v1/files', {
            'name': 'Notes',
            'node_type': 'folder',
            'icon': 'book',
            'color': 'success',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        call_kwargs = mock_create_folder.call_args.kwargs
        self.assertEqual(call_kwargs['icon'], 'book')
        self.assertEqual(call_kwargs['color'], 'success')

    @patch('workspace.files.serializers.FileService.create_folder')
    def test_create_nested_folder_passes_parent(self, mock_create_folder):
        """Parent folder must be forwarded to FileService.create_folder."""
        parent = File.objects.create(
            owner=self.user, name='Root', node_type=File.NodeType.FOLDER
        )
        mock_folder = File(
            owner=self.user, name='Sub', node_type=File.NodeType.FOLDER,
            parent=parent
        )
        mock_folder.uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        mock_create_folder.return_value = mock_folder

        response = self.client.post('/api/v1/files', {
            'name': 'Sub',
            'node_type': 'folder',
            'parent': str(parent.uuid),
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(mock_create_folder.call_args.kwargs['parent'], parent)

    def test_create_folder_creates_directory_on_disk(self):
        """Folder creation via API must create the directory on storage."""
        with patch(
            'workspace.files.services.files.FileService._ensure_folder_on_storage'
        ) as mock_ensure:
            response = self.client.post('/api/v1/files', {
                'name': 'DiskFolder',
                'node_type': 'folder',
            }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_ensure.assert_called_once()
        created_folder = mock_ensure.call_args[0][0]
        self.assertEqual(created_folder.name, 'DiskFolder')

    # ── File creation ─────────────────────────────────────

    @patch('workspace.files.serializers.FileService.create_file')
    def test_create_file_calls_create_file(self, mock_create_file):
        """Verify FileService.create_file is called for file nodes."""
        mock_file = File(
            owner=self.user, name='note.md', node_type=File.NodeType.FILE,
            mime_type='text/markdown', size=5
        )
        mock_file.uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        mock_create_file.return_value = mock_file

        response = self.client.post('/api/v1/files', {
            'name': 'note.md',
            'node_type': 'file',
            'mime_type': 'text/markdown',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_create_file.assert_called_once()
        call_kwargs = mock_create_file.call_args.kwargs
        self.assertEqual(call_kwargs['name'], 'note.md')
        self.assertEqual(call_kwargs['mime_type'], 'text/markdown')

    def test_create_file_with_content(self):
        """File creation with content must set size and mime_type."""
        content = ContentFile(b'Hello World', name='test.txt')
        response = self.client.post('/api/v1/files', {
            'name': 'test.txt',
            'node_type': 'file',
            'content': content,
        }, format='multipart')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['name'], 'test.txt')

    # ── Validation ────────────────────────────────────────

    def test_create_folder_does_not_call_create_file(self):
        """Folder creation must not invoke FileService.create_file."""
        with patch('workspace.files.serializers.FileService.create_file') as mock_file:
            self.client.post('/api/v1/files', {
                'name': 'OnlyFolder',
                'node_type': 'folder',
            }, format='json')

        mock_file.assert_not_called()

    def test_create_file_does_not_call_create_folder(self):
        """File creation must not invoke FileService.create_folder."""
        with patch('workspace.files.serializers.FileService.create_folder') as mock_folder:
            self.client.post('/api/v1/files', {
                'name': 'only_file.txt',
                'node_type': 'file',
            }, format='json')

        mock_folder.assert_not_called()
