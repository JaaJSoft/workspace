from django.contrib import admin, messages

from .models import Notification, PushSubscription
from .services import notify


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
    actions = ['send_test_push']

    @admin.action(description='Send test push notification')
    def send_test_push(self, request, queryset):
        users_sent = set()
        for sub in queryset.select_related('user'):
            if sub.user_id not in users_sent:
                notify(
                    recipient=sub.user,
                    origin='system',
                    icon='bell-ring',
                    title='Test push notification',
                    body='This is a test notification sent from the admin panel.',
                    actor=request.user,
                )
                users_sent.add(sub.user_id)
        count = len(users_sent)
        self.message_user(
            request,
            f'Test push sent to {count} user{"s" if count != 1 else ""}.',
            messages.SUCCESS,
        )
