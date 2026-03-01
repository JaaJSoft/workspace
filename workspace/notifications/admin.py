from django.contrib import admin

from .models import Notification, PushSubscription


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('title', 'recipient', 'origin', 'priority', 'read_at', 'created_at')
    list_filter = ('origin', 'priority', 'read_at')
    search_fields = ('title', 'body', 'recipient__username')
    raw_id_fields = ('recipient', 'actor')
    readonly_fields = ('uuid', 'created_at')
    date_hierarchy = 'created_at'


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'endpoint', 'created_at')
    search_fields = ('user__username', 'endpoint')
    raw_id_fields = ('user',)
    readonly_fields = ('uuid', 'created_at')
