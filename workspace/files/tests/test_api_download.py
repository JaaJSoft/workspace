from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File

User = get_user_model()


class DownloadAPITests(APITestCase):
    """Tests for the download endpoint."""

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

    def test_download_file(self):
        """Test downloading a single file returns attachment disposition."""
        file_obj = File(
            owner=self.user,
            name='report.txt',
            node_type=File.NodeType.FILE,
            mime_type='text/plain',
        )
        file_obj.content = ContentFile(b'Hello World', name='report.txt')
        file_obj.size = 11
        file_obj.save()

        response = self.client.get(f'/api/v1/files/{file_obj.uuid}/download')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('report.txt', response['Content-Disposition'])
        self.assertEqual(b''.join(response.streaming_content), b'Hello World')

    def test_download_file_no_content(self):
        """Test downloading a file without content returns 404."""
        file_obj = File.objects.create(
            owner=self.user,
            name='empty.txt',
            node_type=File.NodeType.FILE,
        )

        response = self.client.get(f'/api/v1/files/{file_obj.uuid}/download')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_download_folder_zip(self):
        """Test downloading a folder returns a valid ZIP with correct content."""
        import zipfile
        import io
        folder = File.objects.create(
            owner=self.user,
            name='Reports',
            node_type=File.NodeType.FOLDER,
        )
        child = File(
            owner=self.user,
            name='data.txt',
            node_type=File.NodeType.FILE,
            parent=folder,
            mime_type='text/plain',
        )
        child.content = ContentFile(b'file content', name='data.txt')
        child.size = 12
        child.save()

        response = self.client.get(f'/api/v1/files/{folder.uuid}/download')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn('Reports.zip', response['Content-Disposition'])

        buf = io.BytesIO(response.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            self.assertIn('data.txt', names)
            self.assertEqual(zf.read('data.txt'), b'file content')

    def test_download_folder_excludes_trashed(self):
        """Test that trashed files inside a folder are excluded from the ZIP."""
        import zipfile
        import io
        folder = File.objects.create(
            owner=self.user,
            name='Mixed',
            node_type=File.NodeType.FOLDER,
        )
        active = File(
            owner=self.user,
            name='active.txt',
            node_type=File.NodeType.FILE,
            parent=folder,
            mime_type='text/plain',
        )
        active.content = ContentFile(b'keep', name='active.txt')
        active.size = 4
        active.save()

        trashed = File(
            owner=self.user,
            name='trashed.txt',
            node_type=File.NodeType.FILE,
            parent=folder,
            mime_type='text/plain',
        )
        trashed.content = ContentFile(b'gone', name='trashed.txt')
        trashed.size = 4
        trashed.save()
        trashed.delete()  # soft-delete

        response = self.client.get(f'/api/v1/files/{folder.uuid}/download')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        buf = io.BytesIO(response.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            self.assertIn('active.txt', names)
            self.assertNotIn('trashed.txt', names)

    def test_download_folder_nested_structure(self):
        """Test that nested folder structure is preserved in the ZIP."""
        import zipfile
        import io
        root = File.objects.create(
            owner=self.user,
            name='Root',
            node_type=File.NodeType.FOLDER,
        )
        sub = File.objects.create(
            owner=self.user,
            name='Sub',
            node_type=File.NodeType.FOLDER,
            parent=root,
        )
        deep_file = File(
            owner=self.user,
            name='deep.txt',
            node_type=File.NodeType.FILE,
            parent=sub,
            mime_type='text/plain',
        )
        deep_file.content = ContentFile(b'nested', name='deep.txt')
        deep_file.size = 6
        deep_file.save()

        response = self.client.get(f'/api/v1/files/{root.uuid}/download')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        buf = io.BytesIO(response.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            self.assertIn('Sub/deep.txt', names)
            self.assertEqual(zf.read('Sub/deep.txt'), b'nested')

    def test_download_other_user_file(self):
        """Test that downloading another user's file returns 404."""
        other_file = File(
            owner=self.other_user,
            name='secret.txt',
            node_type=File.NodeType.FILE,
            mime_type='text/plain',
        )
        other_file.content = ContentFile(b'secret', name='secret.txt')
        other_file.size = 6
        other_file.save()

        response = self.client.get(f'/api/v1/files/{other_file.uuid}/download')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
