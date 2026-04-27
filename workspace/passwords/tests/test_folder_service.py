import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.passwords.models import PasswordFolder, Vault
from workspace.passwords.services.folders import FolderService

User = get_user_model()


class FolderServiceMixin:
    def setUp(self):
        self.user = User.objects.create_user(username='alice_fs', password='pw')
        self.vault = Vault.objects.create(user=self.user, name='Test', kdf_salt='x' * 43)


class ListFoldersTests(FolderServiceMixin, TestCase):

    def test_list_folders_empty(self):
        self.assertEqual(list(FolderService.list_folders(self.vault)), [])

    def test_list_folders_tree(self):
        root = FolderService.create_folder(self.vault, name='Dev')
        child = FolderService.create_folder(self.vault, name='GitHub', parent_uuid=str(root.uuid))
        folders = list(FolderService.list_folders(self.vault))
        uuids = [f.uuid for f in folders]
        self.assertIn(root.uuid, uuids)
        self.assertIn(child.uuid, uuids)


class CreateFolderTests(FolderServiceMixin, TestCase):

    def test_create_root_folder(self):
        folder = FolderService.create_folder(self.vault, name='Dev')
        self.assertEqual(folder.vault, self.vault)
        self.assertIsNone(folder.parent)
        self.assertEqual(folder.name, 'Dev')

    def test_create_child_folder(self):
        parent = FolderService.create_folder(self.vault, name='Dev')
        child = FolderService.create_folder(self.vault, name='GitHub', parent_uuid=str(parent.uuid))
        self.assertEqual(child.parent, parent)

    def test_create_child_wrong_vault_raises(self):
        other_vault = Vault.objects.create(user=self.user, name='Other', kdf_salt='y' * 43)
        other_folder = FolderService.create_folder(other_vault, name='Folder')
        with self.assertRaisesRegex(ValueError, 'parent folder not found'):
            FolderService.create_folder(self.vault, name='Child', parent_uuid=str(other_folder.uuid))


class UpdateFolderTests(FolderServiceMixin, TestCase):

    def test_update_folder(self):
        folder = FolderService.create_folder(self.vault, name='Dev')
        updated = FolderService.update_folder(self.vault, str(folder.uuid), name='Development')
        self.assertEqual(updated.name, 'Development')

    def test_update_folder_not_found(self):
        result = FolderService.update_folder(self.vault, str(uuid.uuid4()), name='X')
        self.assertIsNone(result)


class DeleteFolderTests(FolderServiceMixin, TestCase):

    def test_delete_folder(self):
        folder = FolderService.create_folder(self.vault, name='Dev')
        self.assertTrue(FolderService.delete_folder(self.vault, str(folder.uuid)))
        self.assertFalse(PasswordFolder.objects.filter(uuid=folder.uuid).exists())

    def test_delete_folder_not_found(self):
        self.assertFalse(FolderService.delete_folder(self.vault, str(uuid.uuid4())))
