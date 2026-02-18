from django.contrib import admin

from .models import UserPresence


@admin.register(UserPresence)
class UserPresenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'last_seen', 'last_activity', 'manual_status')
    list_filter = ('last_seen', 'manual_status')
    search_fields = ('user__username', 'user__email')
    raw_id_fields = ('user',)
