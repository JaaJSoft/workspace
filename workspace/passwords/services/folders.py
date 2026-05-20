from ..models import PasswordFolder, Vault


class FolderService:

    @staticmethod
    def list_folders(vault: Vault):
        return PasswordFolder.objects.filter(vault=vault).select_related('parent')

    @staticmethod
    def create_folder(vault: Vault, name: str, parent_uuid: str | None = None) -> PasswordFolder:
        parent = None
        if parent_uuid:
            parent = PasswordFolder.objects.filter(uuid=parent_uuid, vault=vault).first()
            if parent is None:
                raise ValueError('parent folder not found in this vault')
        return PasswordFolder.objects.create(vault=vault, name=name, parent=parent)

    @staticmethod
    def update_folder(vault: Vault, folder_uuid: str, name: str) -> PasswordFolder | None:
        folder = PasswordFolder.objects.filter(uuid=folder_uuid, vault=vault).first()
        if folder is None:
            return None
        folder.name = name
        folder.save(update_fields=['name', 'updated_at'])
        return folder

    @staticmethod
    def delete_folder(vault: Vault, folder_uuid: str) -> bool:
        deleted, _ = PasswordFolder.objects.filter(uuid=folder_uuid, vault=vault).delete()
        return deleted > 0
