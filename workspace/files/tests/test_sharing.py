from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import IntegrityError
from rest_framework.test import APITestCase
from rest_framework import status

from workspace.files.models import File, FileShare

User = get_user_model()


class FileShareModelTests(TestCase):
    """Tests for the FileShare model."""

    def setUp(self):
        self.user_a = User.objects.create_user(
            username='alice', email='alice@example.com', password='pass123'
        )
        self.user_b = User.objects.create_user(
            username='bob', email='bob@example.com', password='pass123'
        )
        self.file = File.objects.create(
            owner=self.user_a, name='doc.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
        )

    def test_create_share(self):
        share = FileShare.objects.create(
            file=self.file, shared_by=self.user_a, shared_with=self.user_b,
        )
        self.assertEqual(share.file, self.file)
        self.assertEqual(share.shared_by, self.user_a)
        self.assertEqual(share.shared_with, self.user_b)

    def test_unique_constraint(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user_a, shared_with=self.user_b,
        )
        with self.assertRaises(IntegrityError):
            FileShare.objects.create(
                file=self.file, shared_by=self.user_a, shared_with=self.user_b,
            )

    def test_cascade_delete_file(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user_a, shared_with=self.user_b,
        )
        self.file.delete(hard=True)
        self.assertEqual(FileShare.objects.count(), 0)

    def test_cascade_delete_user(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user_a, shared_with=self.user_b,
        )
        self.user_b.delete()
        self.assertEqual(FileShare.objects.count(), 0)


class ShareAPITests(APITestCase):
    """Tests for the file sharing API endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='owner', email='owner@example.com', password='pass123'
        )
        self.other_user = User.objects.create_user(
            username='recipient', email='recipient@example.com', password='pass123'
        )
        self.file = File.objects.create(
            owner=self.user, name='test.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
        )
        self.file.content = ContentFile(b'Hello World', name='test.txt')
        self.file.size = 11
        self.file.save()
        self.folder = File.objects.create(
            owner=self.user, name='Docs',
            node_type=File.NodeType.FOLDER,
        )
        self.child_file = File.objects.create(
            owner=self.user, name='child.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
            parent=self.folder,
        )
        self.child_file.content = ContentFile(b'Child content', name='child.txt')
        self.child_file.size = 13
        self.child_file.save()
        self.client.force_authenticate(user=self.user)

    # --- Share ---

    def test_share_file(self):
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.other_user.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            FileShare.objects.filter(
                file=self.file, shared_with=self.other_user,
            ).exists()
        )

    def test_share_folder_rejected(self):
        resp = self.client.post(
            f'/api/v1/files/{self.folder.uuid}/share',
            {'shared_with': self.other_user.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_share_duplicate(self):
        self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.other_user.pk},
            format='json',
        )
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.other_user.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_share_with_self(self):
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.user.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_share_invalid_user(self):
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': 99999},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_share_missing_param(self):
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # --- Unshare ---

    def test_unshare_file(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
        )
        resp = self.client.delete(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.other_user.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(
            FileShare.objects.filter(file=self.file, shared_with=self.other_user).exists()
        )

    def test_unshare_nonexistent(self):
        resp = self.client.delete(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.other_user.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- List shares ---

    def test_list_shares(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
        )
        resp = self.client.get(f'/api/v1/files/{self.file.uuid}/shares')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['username'], 'recipient')

    def test_list_shares_empty(self):
        resp = self.client.get(f'/api/v1/files/{self.file.uuid}/shares')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])

    def test_list_shares_not_owner(self):
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.get(f'/api/v1/files/{self.file.uuid}/shares')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- Shared with me ---

    def test_shared_with_me_empty(self):
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.get('/api/v1/files/shared-with-me')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])

    def test_shared_with_me_lists_shared_files(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
        )
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.get('/api/v1/files/shared-with-me')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['uuid'], str(self.file.uuid))

    def test_shared_with_me_excludes_deleted(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
        )
        self.file.soft_delete()
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.get('/api/v1/files/shared-with-me')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])

    # --- Content / download access ---

    def test_shared_file_content_accessible(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
        )
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.get(f'/api/v1/files/{self.file.uuid}/content')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_shared_file_download_accessible(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
        )
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.get(f'/api/v1/files/{self.file.uuid}/download')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_unshared_file_content_denied(self):
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.get(f'/api/v1/files/{self.file.uuid}/content')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_unshared_file_download_denied(self):
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.get(f'/api/v1/files/{self.file.uuid}/download')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- Isolation ---

    def test_shared_user_cannot_modify(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
        )
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.patch(
            f'/api/v1/files/{self.file.uuid}',
            {'name': 'hacked.txt'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_shared_user_cannot_delete(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
        )
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.delete(f'/api/v1/files/{self.file.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- Permissions ---

    def test_share_with_rw_permission(self):
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.other_user.pk, 'permission': 'rw'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        share = FileShare.objects.get(file=self.file, shared_with=self.other_user)
        self.assertEqual(share.permission, 'rw')

    def test_share_default_ro(self):
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.other_user.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        share = FileShare.objects.get(file=self.file, shared_with=self.other_user)
        self.assertEqual(share.permission, 'ro')

    def test_update_permission(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
            permission='ro',
        )
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.other_user.pk, 'permission': 'rw'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        share = FileShare.objects.get(file=self.file, shared_with=self.other_user)
        self.assertEqual(share.permission, 'rw')

    def test_shares_list_includes_permission(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
            permission='rw',
        )
        resp = self.client.get(f'/api/v1/files/{self.file.uuid}/shares')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['permission'], 'rw')

    def test_share_invalid_permission(self):
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.other_user.pk, 'permission': 'admin'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rw_shared_can_save_content(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
            permission='rw',
        )
        self.client.force_authenticate(user=self.other_user)
        new_content = SimpleUploadedFile('test.txt', b'Updated content', content_type='text/plain')
        resp = self.client.patch(
            f'/api/v1/files/{self.file.uuid}',
            {'content': new_content},
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_ro_shared_cannot_save_content(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
            permission='ro',
        )
        self.client.force_authenticate(user=self.other_user)
        new_content = SimpleUploadedFile('test.txt', b'Updated content', content_type='text/plain')
        resp = self.client.patch(
            f'/api/v1/files/{self.file.uuid}',
            {'content': new_content},
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_rw_shared_cannot_rename(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
            permission='rw',
        )
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.patch(
            f'/api/v1/files/{self.file.uuid}',
            {'name': 'hacked.txt'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_rw_shared_cannot_delete(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
            permission='rw',
        )
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.delete(f'/api/v1/files/{self.file.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- Favorites on shared files ---

    def test_shared_user_can_favorite(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
        )
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.post(f'/api/v1/files/{self.file.uuid}/favorite')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['is_favorite'])

    def test_shared_user_can_unfavorite(self):
        from workspace.files.models import FileFavorite
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
        )
        FileFavorite.objects.create(owner=self.other_user, file=self.file)
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.delete(f'/api/v1/files/{self.file.uuid}/favorite')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['is_favorite'])

    def test_unshared_user_cannot_favorite(self):
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.post(f'/api/v1/files/{self.file.uuid}/favorite')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_shared_favorite_appears_in_favorites_list(self):
        from workspace.files.models import FileFavorite
        FileShare.objects.create(
            file=self.file, shared_by=self.user, shared_with=self.other_user,
        )
        FileFavorite.objects.create(owner=self.other_user, file=self.file)
        self.client.force_authenticate(user=self.other_user)
        resp = self.client.get('/api/v1/files?favorites=1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        uuids = [f['uuid'] for f in resp.data]
        self.assertIn(str(self.file.uuid), uuids)


class UserSearchAPITests(APITestCase):
    """Tests for the user search endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='searcher', email='search@example.com', password='pass123',
            first_name='Search', last_name='User',
        )
        self.alice = User.objects.create_user(
            username='alice_wonder', email='alice@example.com', password='pass123',
            first_name='Alice', last_name='Wonder',
        )
        self.bob = User.objects.create_user(
            username='bob_builder', email='bob@example.com', password='pass123',
            first_name='Bob', last_name='Builder',
        )
        self.inactive = User.objects.create_user(
            username='inactive_user', email='inactive@example.com', password='pass123',
            is_active=False,
        )
        self.client.force_authenticate(user=self.user)

    def test_search_by_username(self):
        resp = self.client.get('/api/v1/users/search?q=alice')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        usernames = [u['username'] for u in resp.data['results']]
        self.assertIn('alice_wonder', usernames)

    def test_search_by_first_name(self):
        resp = self.client.get('/api/v1/users/search?q=Bob')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        usernames = [u['username'] for u in resp.data['results']]
        self.assertIn('bob_builder', usernames)

    def test_search_excludes_self(self):
        resp = self.client.get('/api/v1/users/search?q=searcher')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        usernames = [u['username'] for u in resp.data['results']]
        self.assertNotIn('searcher', usernames)

    def test_search_excludes_inactive(self):
        resp = self.client.get('/api/v1/users/search?q=inactive')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        usernames = [u['username'] for u in resp.data['results']]
        self.assertNotIn('inactive_user', usernames)

    def test_search_min_length(self):
        resp = self.client.get('/api/v1/users/search?q=a')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['results'], [])

    def test_search_limit(self):
        resp = self.client.get('/api/v1/users/search?q=al&limit=1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(resp.data['results']), 1)
