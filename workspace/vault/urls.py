from django.urls import path

from . import views

urlpatterns = [
    path(
        "api/v1/vault/account/init",
        views.AccountInitView.as_view(),
        name="vault-account-init",
    ),
    path(
        "api/v1/vault/account/finalize",
        views.AccountFinalizeView.as_view(),
        name="vault-account-finalize",
    ),
    path(
        "api/v1/vault/account/envelope",
        views.AccountEnvelopeView.as_view(),
        name="vault-account-envelope",
    ),
]
