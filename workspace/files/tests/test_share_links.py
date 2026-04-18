import secrets
from datetime import timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status
from django.core.files.base import ContentFile

from workspace.files.models import File, FileShareLink

User = get_user_model()


class FileShareLinkModelTests(TestCase):
    """Tests for the FileShareLink model."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='owner', email='owner@example.com', password='pass123'
        )
        self.file = File.objects.create(
            owner=self.user, name='doc.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
        )

    def test_create_share_link(self):
        link = FileShareLink.objects.create(
            file=self.file, created_by=self.user,
        )
        self.assertEqual(link.file, self.file)
        self.assertEqual(link.created_by, self.user)
        self.assertTrue(len(link.token) > 0)
        self.assertEqual(link.password, '')
        self.assertIsNone(link.expires_at)
        self.assertEqual(link.view_count, 0)
        self.assertIsNone(link.last_accessed_at)

    def test_token_auto_generated(self):
        link1 = FileShareLink.objects.create(file=self.file, created_by=self.user)
        link2 = FileShareLink.objects.create(file=self.file, created_by=self.user)
        self.assertNotEqual(link1.token, link2.token)

    def test_token_unique_constraint(self):
        token = secrets.token_urlsafe(24)
        FileShareLink.objects.create(file=self.file, created_by=self.user, token=token)
        with self.assertRaises(IntegrityError):
            FileShareLink.objects.create(file=self.file, created_by=self.user, token=token)

    def test_cascade_delete_file(self):
        FileShareLink.objects.create(file=self.file, created_by=self.user)
        self.file.delete(hard=True)
        self.assertEqual(FileShareLink.objects.count(), 0)

    def test_multiple_links_per_file(self):
        FileShareLink.objects.create(file=self.file, created_by=self.user)
        FileShareLink.objects.create(file=self.file, created_by=self.user)
        self.assertEqual(FileShareLink.objects.filter(file=self.file).count(), 2)


class ShareLinkAPITests(APITestCase):
    """Tests for the share link owner API endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='owner', email='owner@example.com', password='pass123'
        )
        self.other_user = User.objects.create_user(
            username='other', email='other@example.com', password='pass123'
        )
        self.file = File.objects.create(
            owner=self.user, name='test.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
        )
        self.file.content = ContentFile(b'Hello World', name='test.txt')
        self.file.size = 11
        self.file.save()

    def test_create_share_link_no_options(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(f'/api/v1/files/{self.file.uuid}/share-links', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn('token', resp.data)
        self.assertIn('url', resp.data)
        self.assertFalse(resp.data['has_password'])
        self.assertIsNone(resp.data['expires_at'])

    def test_create_share_link_with_expiration(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share-links',
            {'expires_at': '2026-12-31T23:59:59Z'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIsNotNone(resp.data['expires_at'])

    def test_create_share_link_with_password(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share-links',
            {'password': 'secret123'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data['has_password'])
        self.assertNotIn('password', resp.data)

    def test_create_share_link_non_owner_forbidden(self):
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.post(f'/api/v1/files/{self.file.uuid}/share-links', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_share_links(self):
        self.client.force_authenticate(user=self.user)
        self.client.post(f'/api/v1/files/{self.file.uuid}/share-links', {}, format='json')
        self.client.post(f'/api/v1/files/{self.file.uuid}/share-links', {}, format='json')
        resp = self.client.get(f'/api/v1/files/{self.file.uuid}/share-links')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 2)

    def test_list_share_links_non_owner_forbidden(self):
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.get(f'/api/v1/files/{self.file.uuid}/share-links')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_share_link_on_folder_rejected(self):
        folder = File.objects.create(
            owner=self.user, name='Docs', node_type=File.NodeType.FOLDER,
        )
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(f'/api/v1/files/{folder.uuid}/share-links', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_share_link(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(f'/api/v1/files/{self.file.uuid}/share-links', {}, format='json')
        link_uuid = resp.data['uuid']
        resp = self.client.delete(f'/api/v1/files/{self.file.uuid}/share-links/{link_uuid}')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(FileShareLink.objects.count(), 0)

    def test_delete_share_link_non_owner_forbidden(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(f'/api/v1/files/{self.file.uuid}/share-links', {}, format='json')
        link_uuid = resp.data['uuid']
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.delete(f'/api/v1/files/{self.file.uuid}/share-links/{link_uuid}')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(FileShareLink.objects.count(), 1)

    def test_delete_nonexistent_share_link(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.delete(f'/api/v1/files/{self.file.uuid}/share-links/00000000-0000-0000-0000-000000000000')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class PublicShareLinkAPITests(APITestCase):
    """Tests for the public (unauthenticated) share link endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='owner', email='owner@example.com', password='pass123'
        )
        self.file = File.objects.create(
            owner=self.user, name='test.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
        )
        self.file.content = ContentFile(b'Hello World', name='test.txt')
        self.file.size = 11
        self.file.save()
        self.link = FileShareLink.objects.create(
            file=self.file, created_by=self.user,
        )

    def test_get_metadata(self):
        resp = self.client.get(f'/api/v1/files/shared/{self.link.token}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'test.txt')
        self.assertEqual(resp.data['mime_type'], 'text/plain')
        self.assertEqual(resp.data['size'], 11)
        self.assertFalse(resp.data['has_password'])
        self.assertIn('is_viewable', resp.data)

    def test_get_metadata_invalid_token(self):
        resp = self.client.get('/api/v1/files/shared/invalid-token-xxx')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_metadata_expired_link(self):
        self.link.expires_at = timezone.now() - timedelta(hours=1)
        self.link.save()
        resp = self.client.get(f'/api/v1/files/shared/{self.link.token}')
        self.assertEqual(resp.status_code, status.HTTP_410_GONE)

    def test_get_metadata_soft_deleted_file(self):
        self.file.soft_delete()
        resp = self.client.get(f'/api/v1/files/shared/{self.link.token}')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_verify_password_correct(self):
        from django.contrib.auth.hashers import make_password
        self.link.password = make_password('secret123')
        self.link.save()
        resp = self.client.post(
            f'/api/v1/files/shared/{self.link.token}/verify',
            {'password': 'secret123'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('access_token', resp.data)

    def test_verify_password_incorrect(self):
        from django.contrib.auth.hashers import make_password
        self.link.password = make_password('secret123')
        self.link.save()
        resp = self.client.post(
            f'/api/v1/files/shared/{self.link.token}/verify',
            {'password': 'wrongpass'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_verify_no_password_set(self):
        resp = self.client.post(
            f'/api/v1/files/shared/{self.link.token}/verify',
            {'password': 'anything'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_content_no_password(self):
        resp = self.client.get(f'/api/v1/files/shared/{self.link.token}/content')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn(b'Hello World', resp.content)

    def test_get_content_increments_view_count(self):
        self.client.get(f'/api/v1/files/shared/{self.link.token}/content')
        self.link.refresh_from_db()
        self.assertEqual(self.link.view_count, 1)
        self.assertIsNotNone(self.link.last_accessed_at)

    def test_get_content_password_required(self):
        from django.contrib.auth.hashers import make_password
        self.link.password = make_password('secret123')
        self.link.save()
        resp = self.client.get(f'/api/v1/files/shared/{self.link.token}/content')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_content_with_valid_access_token(self):
        from django.contrib.auth.hashers import make_password
        self.link.password = make_password('secret123')
        self.link.save()
        verify_resp = self.client.post(
            f'/api/v1/files/shared/{self.link.token}/verify',
            {'password': 'secret123'}, format='json',
        )
        access_token = verify_resp.data['access_token']
        resp = self.client.get(
            f'/api/v1/files/shared/{self.link.token}/content?access_token={access_token}'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_download_file(self):
        resp = self.client.get(f'/api/v1/files/shared/{self.link.token}/download')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('attachment', resp.get('Content-Disposition', ''))

    def test_download_increments_view_count(self):
        self.client.get(f'/api/v1/files/shared/{self.link.token}/download')
        self.link.refresh_from_db()
        self.assertEqual(self.link.view_count, 1)

    def test_content_expired_link(self):
        self.link.expires_at = timezone.now() - timedelta(hours=1)
        self.link.save()
        resp = self.client.get(f'/api/v1/files/shared/{self.link.token}/content')
        self.assertEqual(resp.status_code, status.HTTP_410_GONE)

    def test_verify_rate_limited(self):
        from django.contrib.auth.hashers import make_password
        self.link.password = make_password('secret123')
        self.link.save()
        for i in range(5):
            self.client.post(
                f'/api/v1/files/shared/{self.link.token}/verify',
                {'password': 'wrong'}, format='json',
            )
        resp = self.client.post(
            f'/api/v1/files/shared/{self.link.token}/verify',
            {'password': 'wrong'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class ShareLinkIntegrationTests(APITestCase):
    """End-to-end tests for the share link feature."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='owner', email='owner@example.com', password='pass123'
        )
        self.file = File.objects.create(
            owner=self.user, name='readme.md',
            node_type=File.NodeType.FILE, mime_type='text/markdown',
        )
        self.file.content = ContentFile(b'# Hello', name='readme.md')
        self.file.size = 7
        self.file.save()

    def test_full_flow_no_password(self):
        """Owner creates link -> external user views and downloads."""
        self.client.force_authenticate(user=self.user)
        # Create link
        resp = self.client.post(f'/api/v1/files/{self.file.uuid}/share-links', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        token = resp.data['token']
        # External user (no auth)
        self.client.force_authenticate(user=None)
        # Get metadata
        resp = self.client.get(f'/api/v1/files/shared/{token}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'readme.md')
        # Get content
        resp = self.client.get(f'/api/v1/files/shared/{token}/content')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Download
        resp = self.client.get(f'/api/v1/files/shared/{token}/download')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_full_flow_with_password(self):
        """Owner creates password-protected link -> external user verifies then views."""
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share-links',
            {'password': 'mypass'},
            format='json',
        )
        token = resp.data['token']
        self.client.force_authenticate(user=None)
        # Content blocked without password
        resp = self.client.get(f'/api/v1/files/shared/{token}/content')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        # Verify password
        resp = self.client.post(
            f'/api/v1/files/shared/{token}/verify',
            {'password': 'mypass'}, format='json',
        )
        access_token = resp.data['access_token']
        # Content accessible with token
        resp = self.client.get(f'/api/v1/files/shared/{token}/content?access_token={access_token}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_revoke_link_blocks_access(self):
        """Owner revokes link -> external user gets 404."""
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(f'/api/v1/files/{self.file.uuid}/share-links', {}, format='json')
        token = resp.data['token']
        link_uuid = resp.data['uuid']
        # Revoke
        self.client.delete(f'/api/v1/files/{self.file.uuid}/share-links/{link_uuid}')
        # External user
        self.client.force_authenticate(user=None)
        resp = self.client.get(f'/api/v1/files/shared/{token}')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
