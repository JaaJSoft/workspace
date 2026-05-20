from django.urls import path

from .views import (
    FolderDetailView,
    FolderListCreateView,
    LoginEntryDetailView,
    LoginEntryListCreateView,
    VaultDetailView,
    VaultListCreateView,
    VaultSetupView,
    VaultUnlockView,
)

urlpatterns = [
    # Vault CRUD
    path('api/v1/passwords/vaults', VaultListCreateView.as_view(), name='passwords-vaults'),
    path('api/v1/passwords/vaults/<uuid:uuid>', VaultDetailView.as_view(), name='passwords-vault-detail'),
    # Vault lifecycle actions
    path('api/v1/passwords/vaults/<uuid:uuid>/setup', VaultSetupView.as_view(), name='passwords-vault-setup'),
    path('api/v1/passwords/vaults/<uuid:uuid>/unlock', VaultUnlockView.as_view(), name='passwords-vault-unlock'),
    # Folders
    path('api/v1/passwords/vaults/<uuid:uuid>/folders', FolderListCreateView.as_view(), name='passwords-vault-folders'),
    path('api/v1/passwords/folders/<uuid:uuid>', FolderDetailView.as_view(), name='passwords-folder-detail'),
    # Entries
    path('api/v1/passwords/entries', LoginEntryListCreateView.as_view(), name='passwords-entries'),
    path('api/v1/passwords/entries/<uuid:uuid>', LoginEntryDetailView.as_view(), name='passwords-entry-detail'),
]
