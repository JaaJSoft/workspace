import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.passwords.models import PasswordFolder, Vault

User = get_user_model()


class FolderViewTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='alice_fv', password='pw')
        self.vault = Vault.objects.create(user=self.user, name='Test', kdf_salt='x' * 43, is_setup=True)

    def auth(self):
        self.client.force_authenticate(user=self.user)


class FolderListCreateTests(FolderViewTestBase):

    def test_list_folders_empty(self):
        self.auth()
        resp = self.client.get(f'/api/v1/passwords/vaults/{self.vault.uuid}/folders')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_create_folder(self):
        self.auth()
        resp = self.client.post(
            f'/api/v1/passwords/vaults/{self.vault.uuid}/folders',
            {'name': 'Dev'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['name'], 'Dev')
        self.assertIsNone(resp.json()['parent'])

    def test_create_folder_unauthenticated(self):
        resp = self.client.post(
            f'/api/v1/passwords/vaults/{self.vault.uuid}/folders',
            {'name': 'X'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_create_folder_other_user_vault(self):
        other = User.objects.create_user(username='bob_fv', password='pw')
        self.client.force_authenticate(user=other)
        resp = self.client.post(
            f'/api/v1/passwords/vaults/{self.vault.uuid}/folders',
            {'name': 'X'},
            format='json',
        )
        self.assertEqual(resp.status_code, 404)


class FolderDetailTests(FolderViewTestBase):

    def test_update_folder(self):
        self.auth()
        folder = PasswordFolder.objects.create(vault=self.vault, name='Dev')
        resp = self.client.put(
            f'/api/v1/passwords/folders/{folder.uuid}',
            {'name': 'Development'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['name'], 'Development')

    def test_delete_folder(self):
        self.auth()
        folder = PasswordFolder.objects.create(vault=self.vault, name='Dev')
        resp = self.client.delete(f'/api/v1/passwords/folders/{folder.uuid}')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(PasswordFolder.objects.filter(uuid=folder.uuid).exists())

    def test_delete_folder_other_user(self):
        folder = PasswordFolder.objects.create(vault=self.vault, name='Dev')
        other = User.objects.create_user(username='charlie_fv', password='pw')
        self.client.force_authenticate(user=other)
        resp = self.client.delete(f'/api/v1/passwords/folders/{folder.uuid}')
        self.assertEqual(resp.status_code, 404)
