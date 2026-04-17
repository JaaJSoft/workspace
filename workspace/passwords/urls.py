from django.urls import path

from .views import (
    LoginEntryDetailView,
    LoginEntryListCreateView,
    VaultSetupView,
    VaultUnlockView,
    VaultView,
)

urlpatterns = [
    path('api/v1/passwords/vault', VaultView.as_view(), name='passwords-vault'),
    path('api/v1/passwords/vault/setup', VaultSetupView.as_view(), name='passwords-vault-setup'),
    path('api/v1/passwords/vault/unlock', VaultUnlockView.as_view(), name='passwords-vault-unlock'),
    path('api/v1/passwords/entries', LoginEntryListCreateView.as_view(), name='passwords-entries'),
    path('api/v1/passwords/entries/<uuid:uuid>', LoginEntryDetailView.as_view(), name='passwords-entry-detail'),
]
