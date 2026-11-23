"""Read-only admin over the vault tables.

Everything meaningful in these rows is end-to-end encrypted and signed by the
client; a server-side edit could only break a signature and read as tampering
in the UI. The admin therefore exposes the plaintext envelope (owners,
memberships, key versions, timestamps) for diagnostics, and allows deletion
for cleanup - never creation or edition.

Deleting an account identity takes along what only it could open (see
``services/identities.py``): a row left behind would be ciphertext nobody can
read, listed to its owner as a vault that never opens.
"""

from django.contrib import admin
from django.contrib.admin.utils import get_deleted_objects
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import AccountIdentity, Vault, VaultEntry, VaultKeyWrap
from .services.identities import delete_identity, sealed_to


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

    def delete_model(self, request, obj):
        delete_identity(obj)

    def delete_queryset(self, request, queryset):
        for identity in queryset:
            delete_identity(identity)

    def get_deleted_objects(self, objs, request):
        # The confirmation page lists what Django cascades, and an identity
        # cascades nothing: what goes with it is added here.
        to_delete, counts, perms_needed, protected = super().get_deleted_objects(
            objs, request
        )
        for identity in objs:
            wraps, vaults = sealed_to(identity)
            for rows in (vaults, wraps.exclude(vault__in=vaults)):
                listed, more, perms, kept = get_deleted_objects(
                    rows, request, self.admin_site
                )
                to_delete += listed
                for name, count in more.items():
                    counts[name] = counts.get(name, 0) + count
                perms_needed |= perms
                protected += kept
        return to_delete, counts, perms_needed, protected


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
