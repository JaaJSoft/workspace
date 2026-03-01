from django.contrib import admin

from .models import UserPresence, UserSetting


@admin.register(UserPresence)
class UserPresenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'last_seen', 'last_activity', 'manual_status')
    list_filter = ('last_seen', 'manual_status')
    search_fields = ('user__username', 'user__email')
    raw_id_fields = ('user',)


@admin.register(UserSetting)
class UserSettingAdmin(admin.ModelAdmin):
    list_display = ('user', 'module', 'key', 'value', 'updated_at')
    list_filter = ('module',)
    search_fields = ('user__username', 'key')
    raw_id_fields = ('user',)
    readonly_fields = ('uuid', 'created_at', 'updated_at')
