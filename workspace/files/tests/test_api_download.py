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

        buf = io.BytesIO(b''.join(response.streaming_content))
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

        buf = io.BytesIO(b''.join(response.streaming_content))
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

        buf = io.BytesIO(b''.join(response.streaming_content))
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


class BulkDownloadAPITests(APITestCase):
    """Tests for POST /api/v1/files/bulk-download."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='bulkuser', email='bulk@example.com', password='pass',
        )
        self.client.force_authenticate(user=self.user)
        # Two flat files at the user root
        self.file_a = File(
            owner=self.user, name='a.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
        )
        self.file_a.content = ContentFile(b'AAAA', name='a.txt')
        self.file_a.size = 4
        self.file_a.save()
        self.file_b = File(
            owner=self.user, name='b.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
        )
        self.file_b.content = ContentFile(b'BBBB', name='b.txt')
        self.file_b.size = 4
        self.file_b.save()

    def _read_zip(self, response):
        import io
        import zipfile
        if hasattr(response, 'streaming_content'):
            body = b''.join(response.streaming_content)
        else:
            body = response.content
        return zipfile.ZipFile(io.BytesIO(body))

    def test_bulk_download_two_flat_files(self):
        resp = self.client.post(
            '/api/v1/files/bulk-download',
            {'uuids': [str(self.file_a.uuid), str(self.file_b.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'application/zip')
        self.assertIn('download.zip', resp['Content-Disposition'])
        with self._read_zip(resp) as zf:
            names = zf.namelist()
            self.assertIn('a.txt', names)
            self.assertIn('b.txt', names)
            self.assertEqual(zf.read('a.txt'), b'AAAA')
            self.assertEqual(zf.read('b.txt'), b'BBBB')

    def test_bulk_download_includes_folder_descendants(self):
        folder = File.objects.create(
            owner=self.user, name='Docs', node_type=File.NodeType.FOLDER,
        )
        child = File(
            owner=self.user, name='inside.txt', parent=folder,
            node_type=File.NodeType.FILE, mime_type='text/plain',
        )
        child.content = ContentFile(b'inside-content', name='inside.txt')
        child.size = 14
        child.save()

        resp = self.client.post(
            '/api/v1/files/bulk-download',
            {'uuids': [str(folder.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        with self._read_zip(resp) as zf:
            names = zf.namelist()
            self.assertIn('Docs/inside.txt', names)
            self.assertEqual(zf.read('Docs/inside.txt'), b'inside-content')

    def test_bulk_download_rejects_empty_list(self):
        resp = self.client.post(
            '/api/v1/files/bulk-download', {'uuids': []}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_download_rejects_over_200(self):
        too_many = [str(self.file_a.uuid)] * 201
        resp = self.client.post(
            '/api/v1/files/bulk-download', {'uuids': too_many}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_download_unknown_uuid_returns_404(self):
        resp = self.client.post(
            '/api/v1/files/bulk-download',
            {'uuids': [
                str(self.file_a.uuid),
                '00000000-0000-0000-0000-000000000000',
            ]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_bulk_download_response_is_streaming(self):
        """Regression guard: a buffered BytesIO impl would re-introduce OOM risk
        for large archives. The response must stream the ZIP."""
        from django.http import StreamingHttpResponse
        resp = self.client.post(
            '/api/v1/files/bulk-download',
            {'uuids': [str(self.file_a.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp, StreamingHttpResponse)
        # Also assert the same for the single-folder /download path
        folder = File.objects.create(
            owner=self.user, name='Stream', node_type=File.NodeType.FOLDER,
        )
        child = File(
            owner=self.user, name='c.txt', parent=folder,
            node_type=File.NodeType.FILE, mime_type='text/plain',
        )
        child.content = ContentFile(b'streamed', name='c.txt')
        child.size = 8
        child.save()
        resp = self.client.get(f'/api/v1/files/{folder.uuid}/download')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp, StreamingHttpResponse)
