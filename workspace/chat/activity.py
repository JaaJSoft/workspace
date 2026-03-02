from datetime import date

from django.db.models import Count, Q
from django.db.models.functions import TruncDate

from workspace.core.activity_registry import ActivityProvider


class ChatActivityProvider(ActivityProvider):

    def _visibility_filter(self, user_id, viewer_id):
        """Restrict to conversations where viewer is also a member."""
        if viewer_id is None or viewer_id == user_id:
            return Q()
        from workspace.chat.models import ConversationMember
        visible_conv_ids = ConversationMember.objects.filter(
            user_id=viewer_id,
            left_at__isnull=True,
        ).values_list('conversation_id', flat=True)
        return Q(conversation_id__in=visible_conv_ids)

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        from workspace.chat.models import Message

        qs = Message.objects.filter(
            deleted_at__isnull=True,
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        if user_id is not None:
            qs = qs.filter(author_id=user_id)
        qs = qs.filter(self._visibility_filter(user_id, viewer_id))

        rows = qs.annotate(day=TruncDate('created_at')).values('day').annotate(
            count=Count('pk'),
        ).order_by('day')

        return {row['day']: row['count'] for row in rows}

    def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
        from workspace.chat.models import Message

        qs = Message.objects.filter(
            deleted_at__isnull=True,
        )
        if user_id is not None:
            qs = qs.filter(author_id=user_id)
        qs = qs.filter(
            self._visibility_filter(user_id, viewer_id),
        ).select_related(
            'author', 'conversation',
        ).order_by('-created_at')[offset:offset + limit]

        events = []
        for msg in qs:
            conv_title = msg.conversation.title or 'Direct message'
            events.append({
                'icon': 'message-circle',
                'label': 'Message sent',
                'description': f'{conv_title}: {msg.body[:80]}',
                'timestamp': msg.created_at,
                'url': f'/chat/{msg.conversation_id}',
                'actor': {
                    'id': msg.author_id,
                    'username': msg.author.username,
                    'full_name': msg.author.get_full_name(),
                },
            })
        return events

    def get_stats(self, user_id, *, viewer_id=None):
        from workspace.chat.models import Message, ConversationMember

        qs = Message.objects.filter(deleted_at__isnull=True)
        if user_id is not None:
            qs = qs.filter(author_id=user_id)
        msg_count = qs.filter(self._visibility_filter(user_id, viewer_id)).count()

        conv_filter = {}
        if user_id is not None:
            conv_filter['user_id'] = user_id
        conv_count = ConversationMember.objects.filter(
            left_at__isnull=True,
            **conv_filter,
        ).count()

        return {
            'total_messages': msg_count,
            'active_conversations': conv_count,
        }
