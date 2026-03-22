from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from rest_framework.test import APITestCase
from rest_framework import status

from workspace.files.models import File, Tag, FileTag

User = get_user_model()


class TagModelTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@example.com', password='testpass123',
        )

    def test_create_tag(self):
        tag = Tag.objects.create(owner=self.user, name='important', color='primary')
        self.assertEqual(tag.name, 'important')
        self.assertEqual(tag.color, 'primary')
        self.assertEqual(tag.owner, self.user)
        self.assertIsNotNone(tag.uuid)

    def test_tag_unique_per_user(self):
        Tag.objects.create(owner=self.user, name='work', color='accent')
        with self.assertRaises(IntegrityError):
            Tag.objects.create(owner=self.user, name='work', color='info')

    def test_tag_same_name_different_users(self):
        other = User.objects.create_user(
            username='other', email='other@example.com', password='testpass123',
        )
        Tag.objects.create(owner=self.user, name='work', color='accent')
        tag2 = Tag.objects.create(owner=other, name='work', color='info')
        self.assertEqual(tag2.name, 'work')


class FileTagModelTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@example.com', password='testpass123',
        )
        self.tag = Tag.objects.create(owner=self.user, name='important', color='primary')
        self.file = File.objects.create(
            owner=self.user, name='note.md', node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )

    def test_add_tag_to_file(self):
        ft = FileTag.objects.create(file=self.file, tag=self.tag)
        self.assertEqual(ft.file, self.file)
        self.assertEqual(ft.tag, self.tag)

    def test_unique_file_tag(self):
        FileTag.objects.create(file=self.file, tag=self.tag)
        with self.assertRaises(IntegrityError):
            FileTag.objects.create(file=self.file, tag=self.tag)

    def test_cascade_delete_file(self):
        FileTag.objects.create(file=self.file, tag=self.tag)
        self.file.delete(hard=True)
        self.assertEqual(FileTag.objects.count(), 0)

    def test_cascade_delete_tag(self):
        FileTag.objects.create(file=self.file, tag=self.tag)
        self.tag.delete()
        self.assertEqual(FileTag.objects.count(), 0)


class TagAPITests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@example.com', password='testpass123',
        )
        self.client.force_authenticate(self.user)

    def test_list_tags(self):
        Tag.objects.create(owner=self.user, name='work', color='primary')
        Tag.objects.create(owner=self.user, name='personal', color='accent')
        resp = self.client.get('/api/v1/tags')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 2)

    def test_list_tags_only_own(self):
        other = User.objects.create_user(
            username='other', email='other@example.com', password='testpass123',
        )
        Tag.objects.create(owner=self.user, name='mine', color='primary')
        Tag.objects.create(owner=other, name='theirs', color='accent')
        resp = self.client.get('/api/v1/tags')
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['name'], 'mine')

    def test_create_tag(self):
        resp = self.client.post('/api/v1/tags', {'name': 'urgent', 'color': 'error'})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['name'], 'urgent')
        self.assertEqual(Tag.objects.filter(owner=self.user).count(), 1)

    def test_create_tag_duplicate_name(self):
        Tag.objects.create(owner=self.user, name='work', color='primary')
        resp = self.client.post('/api/v1/tags', {'name': 'work', 'color': 'accent'})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_tag(self):
        tag = Tag.objects.create(owner=self.user, name='old', color='primary')
        resp = self.client.patch(f'/api/v1/tags/{tag.uuid}', {'name': 'new'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        tag.refresh_from_db()
        self.assertEqual(tag.name, 'new')

    def test_delete_tag(self):
        tag = Tag.objects.create(owner=self.user, name='temp', color='ghost')
        resp = self.client.delete(f'/api/v1/tags/{tag.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Tag.objects.filter(uuid=tag.uuid).exists())

    def test_cannot_update_other_users_tag(self):
        other = User.objects.create_user(
            username='other', email='other@example.com', password='testpass123',
        )
        tag = Tag.objects.create(owner=other, name='theirs', color='primary')
        resp = self.client.patch(f'/api/v1/tags/{tag.uuid}', {'name': 'mine now'})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class FileTagAPITests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@example.com', password='testpass123',
        )
        self.client.force_authenticate(self.user)
        self.file = File.objects.create(
            owner=self.user, name='note.md', node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )
        self.tag = Tag.objects.create(owner=self.user, name='work', color='primary')

    def test_add_tag_to_file(self):
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/tags',
            {'tag': str(self.tag.uuid)},
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(FileTag.objects.filter(file=self.file, tag=self.tag).exists())

    def test_add_tag_duplicate(self):
        FileTag.objects.create(file=self.file, tag=self.tag)
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/tags',
            {'tag': str(self.tag.uuid)},
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_add_other_users_tag_forbidden(self):
        other = User.objects.create_user(
            username='other', email='other@example.com', password='testpass123',
        )
        other_tag = Tag.objects.create(owner=other, name='theirs', color='accent')
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/tags',
            {'tag': str(other_tag.uuid)},
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_remove_tag_from_file(self):
        FileTag.objects.create(file=self.file, tag=self.tag)
        resp = self.client.delete(
            f'/api/v1/files/{self.file.uuid}/tags/{self.tag.uuid}',
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(FileTag.objects.filter(file=self.file, tag=self.tag).exists())

    def test_remove_nonexistent_tag(self):
        resp = self.client.delete(
            f'/api/v1/files/{self.file.uuid}/tags/{self.tag.uuid}',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class FileTagFilterTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@example.com', password='testpass123',
        )
        self.client.force_authenticate(self.user)
        self.tag1 = Tag.objects.create(owner=self.user, name='work', color='primary')
        self.tag2 = Tag.objects.create(owner=self.user, name='personal', color='accent')
        self.file1 = File.objects.create(
            owner=self.user, name='note1.md', node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )
        self.file2 = File.objects.create(
            owner=self.user, name='note2.md', node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )
        FileTag.objects.create(file=self.file1, tag=self.tag1)
        FileTag.objects.create(file=self.file2, tag=self.tag2)

    def test_file_list_includes_tags(self):
        resp = self.client.get('/api/v1/files?recent=1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        file1_data = next(f for f in resp.data if f['uuid'] == str(self.file1.uuid))
        self.assertEqual(len(file1_data['tags']), 1)
        self.assertEqual(file1_data['tags'][0]['name'], 'work')

    def test_filter_by_tag(self):
        resp = self.client.get(f'/api/v1/files?recent=1&tags={self.tag1.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['uuid'], str(self.file1.uuid))

    def test_filter_by_mime_type(self):
        File.objects.create(
            owner=self.user, name='image.png', node_type=File.NodeType.FILE,
            mime_type='image/png',
        )
        resp = self.client.get('/api/v1/files?recent=1&mime_type=text/markdown')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = {f['name'] for f in resp.data}
        self.assertIn('note1.md', names)
        self.assertNotIn('image.png', names)
