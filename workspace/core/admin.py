"""Re-register third-party admins on top of unfold's ModelAdmin.

Unfold only styles change forms and lists whose ModelAdmin inherits from
unfold.admin.ModelAdmin, so admins registered by django.contrib.auth and knox
have to be swapped for unfold-based equivalents. This module loads after those
apps (INSTALLED_APPS order drives admin autodiscovery), making the unregister
calls safe.
"""

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group, User
from knox.admin import AuthTokenAdmin as KnoxAuthTokenAdmin
from knox.models import AuthToken
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

admin.site.unregister(User)
admin.site.unregister(Group)
admin.site.unregister(AuthToken)


@admin.register(User)
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin, ModelAdmin):
    pass


@admin.register(AuthToken)
class AuthTokenAdmin(KnoxAuthTokenAdmin, ModelAdmin):
    pass
