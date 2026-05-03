from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileFavorite, PinnedFolder

User = get_user_model()


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
        File.objects.create(
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
        File.objects.create(
            owner=self.user,
            name='NotPinned',
            node_type=File.NodeType.FOLDER
        )
        PinnedFolder.objects.create(owner=self.user, folder=folder1, position=0)

        response = self.client.get('/api/v1/files/pinned')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['uuid'], str(folder1.uuid))

    def test_list_includes_pinned_group_subfolder(self):
        """Pinned group subfolders must appear in the listing.

        Regression: ``pinned`` previously used the owner-scoped queryset
        (``group__isnull=True``), so a successfully pinned group subfolder
        was silently dropped from the sidebar listing.
        """
        group = Group.objects.create(name='Marketing')
        self.user.groups.add(group)
        group_root = File.objects.create(
            owner=self.user, name='Marketing', node_type=File.NodeType.FOLDER, group=group,
        )
        group_sub = File.objects.create(
            owner=self.user, name='Reports', node_type=File.NodeType.FOLDER,
            group=group, parent=group_root,
        )
        PinnedFolder.objects.create(owner=self.user, folder=group_sub, position=0)

        response = self.client.get('/api/v1/files/pinned')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        uuids = [item['uuid'] for item in response.data]
        self.assertIn(str(group_sub.uuid), uuids)

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
