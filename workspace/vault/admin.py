"""Read-only admin over the vault tables.

Everything meaningful in these rows is end-to-end encrypted and signed by the
client; a server-side edit could only break a signature and read as tampering
in the UI. The admin therefore exposes the plaintext envelope (owners,
memberships, key versions, timestamps) for diagnostics, and allows deletion
for cleanup - never creation or edition.
"""

from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import AccountIdentity, Vault, VaultEntry, VaultKeyWrap


class ReadOnlyAdmin(ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AccountIdentity)
class AccountIdentityAdmin(ReadOnlyAdmin):
    list_display = ("user", "state_badge", "kdf_algo", "created_at", "updated_at")
    list_filter = ("state",)
    list_select_related = ("user",)
    search_fields = ("user__username", "user__email")

    @display(
        description="State",
        label={
            AccountIdentity.State.ACTIVE: "success",
            AccountIdentity.State.PENDING: "warning",
        },
    )
    def state_badge(self, obj):
        return obj.state


@admin.register(Vault)
class VaultAdmin(ReadOnlyAdmin):
    list_display = ("uuid", "owner", "key_version", "is_favorite", "created_at")
    list_select_related = ("owner",)
    search_fields = ("uuid", "owner__username")


@admin.register(VaultKeyWrap)
class VaultKeyWrapAdmin(ReadOnlyAdmin):
    list_display = ("vault", "recipient", "key_version", "created_at")
    list_select_related = ("vault", "recipient")
    search_fields = ("vault__uuid", "recipient__username")


@admin.register(VaultEntry)
class VaultEntryAdmin(ReadOnlyAdmin):
    list_display = (
        "uuid",
        "vault",
        "type",
        "is_favorite",
        "deleted_at",
        "last_used_at",
        "created_at",
    )
    list_filter = ("type",)
    list_select_related = ("vault",)
    search_fields = ("uuid", "vault__uuid", "vault__owner__username")
    date_hierarchy = "created_at"
