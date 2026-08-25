from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group, User
from knox.admin import AuthTokenAdmin as KnoxAuthTokenAdmin
from knox.models import AuthToken
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from workspace.files.admin import GroupStorageQuotaInline, UserStorageQuotaInline

from .models import APITokenLabel, UserPresence, UserSetting


@admin.register(UserPresence)
class UserPresenceAdmin(ModelAdmin):
    list_display = ("user", "last_seen", "last_activity", "manual_status")
    list_filter = ("last_seen", "manual_status")
    list_select_related = ("user",)
    search_fields = ("user__username", "user__email")
    autocomplete_fields = ("user",)


@admin.register(UserSetting)
class UserSettingAdmin(ModelAdmin):
    list_display = ("user", "module", "key", "value", "updated_at")
    list_filter = ("module",)
    list_select_related = ("user",)
    search_fields = ("user__username", "key")
    autocomplete_fields = ("user",)
    readonly_fields = ("uuid", "created_at", "updated_at")


@admin.register(APITokenLabel)
class APITokenLabelAdmin(ModelAdmin):
    list_display = ("name", "auth_token")
    list_select_related = ("auth_token",)
    search_fields = ("name",)
    raw_id_fields = ("auth_token",)
    readonly_fields = ("uuid",)


# The User, Group and knox AuthToken admins are registered by their own apps
# on the plain django.contrib.admin.ModelAdmin, which unfold leaves unstyled.
# Re-register them on unfold bases; this module loads after those apps
# (INSTALLED_APPS order drives admin autodiscovery), so the unregister calls
# are safe.
admin.site.unregister(User)
admin.site.unregister(Group)
admin.site.unregister(AuthToken)


@admin.register(User)
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    inlines = (UserStorageQuotaInline,)
    list_display = (
        *DjangoUserAdmin.list_display,
        "is_active",
        "last_login",
        "date_joined",
    )


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin, ModelAdmin):
    inlines = (GroupStorageQuotaInline,)
    search_fields = ("name",)


@admin.register(AuthToken)
class AuthTokenAdmin(KnoxAuthTokenAdmin, ModelAdmin):
    pass
