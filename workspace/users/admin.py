from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group, User
from knox.admin import AuthTokenAdmin as KnoxAuthTokenAdmin
from knox.models import AuthToken
from unfold.admin import ModelAdmin, StackedInline
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from workspace.files.admin import GroupStorageQuotaInline, UserStorageQuotaInline

from .models import APITokenLabel, GroupVisibility, UserPresence, UserSetting
from .validators import validate_username


@admin.register(UserPresence)
class UserPresenceAdmin(ModelAdmin):
    list_display = ("user", "last_seen", "manual_status")
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


class WorkspaceUserCreationForm(UserCreationForm):
    def clean_username(self):
        username = super().clean_username()
        if username is not None:
            validate_username(username)
        return username


class WorkspaceUserChangeForm(UserChangeForm):
    def clean_username(self):
        username = self.cleaned_data["username"]
        # An account named so before the rule existed stays editable.
        if "username" in self.changed_data:
            validate_username(username)
        return username


@admin.register(User)
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    form = WorkspaceUserChangeForm
    add_form = WorkspaceUserCreationForm
    change_password_form = AdminPasswordChangeForm
    inlines = (UserStorageQuotaInline,)
    list_display = (
        *DjangoUserAdmin.list_display,
        "is_active",
        "last_login",
        "date_joined",
    )


class GroupVisibilityInline(StackedInline):
    """The hidden flag, on the group's own page. The one blank form saves
    nothing until the box is ticked, so a visible group keeps no row."""

    model = GroupVisibility
    extra = 1
    max_num = 1
    can_delete = False
    fields = ("is_hidden",)


class HiddenGroupFilter(admin.SimpleListFilter):
    """A visible group has no row at all, which a plain filter on
    ``visibility__is_hidden`` would leave out of "No"."""

    title = "hidden"
    parameter_name = "hidden"

    def lookups(self, request, model_admin):
        return (("yes", "Yes"), ("no", "No"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(visibility__is_hidden=True)
        if self.value() == "no":
            return queryset.exclude(visibility__is_hidden=True)
        return queryset


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin, ModelAdmin):
    inlines = (GroupVisibilityInline, GroupStorageQuotaInline)
    list_display = ("name", "is_hidden")
    list_filter = (HiddenGroupFilter,)
    list_select_related = ("visibility",)
    search_fields = ("name",)

    @admin.display(boolean=True, description="Hidden", ordering="visibility__is_hidden")
    def is_hidden(self, group):
        visibility = getattr(group, "visibility", None)
        return visibility is not None and visibility.is_hidden


@admin.register(AuthToken)
class AuthTokenAdmin(KnoxAuthTokenAdmin, ModelAdmin):
    pass
