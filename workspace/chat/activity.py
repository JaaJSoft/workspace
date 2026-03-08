from django.db.models import Count, Q
from django.db.models.functions import TruncDate

from workspace.core.activity_registry import ActivityProvider


class ChatActivityProvider(ActivityProvider):

    def _base_qs(self, user_id, viewer_id):
        from workspace.chat.models import Conversation
        from workspace.chat.services import user_conversation_ids

        qs = Conversation.objects.filter(kind=Conversation.Kind.GROUP)
        if user_id is not None:
            qs = qs.filter(created_by_id=user_id)
        if viewer_id is not None and viewer_id != user_id:
            qs = qs.filter(pk__in=user_conversation_ids(viewer_id))
        return qs

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        qs = self._base_qs(user_id, viewer_id).filter(
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        rows = qs.annotate(day=TruncDate('created_at')).values('day').annotate(
            count=Count('pk'),
        ).order_by('day')
        return {row['day']: row['count'] for row in rows}

    def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
        qs = self._base_qs(user_id, viewer_id).select_related(
            'created_by',
        ).order_by('-created_at')[offset:offset + limit]

        events = []
        for conv in qs:
            events.append({
                'icon': 'message-circle-plus',
                'label': 'Group created',
                'description': conv.title or 'Untitled group',
                'timestamp': conv.created_at,
                'url': f'/chat/{conv.pk}',
                'actor': {
                    'id': conv.created_by_id,
                    'username': conv.created_by.username,
                    'full_name': conv.created_by.get_full_name(),
                },
            })
        return events

    def get_stats(self, user_id, *, viewer_id=None):
        from workspace.chat.models import Message, ConversationMember

        conv_filter = {}
        if user_id is not None:
            conv_filter['user_id'] = user_id
        conv_count = ConversationMember.objects.filter(
            left_at__isnull=True,
            **conv_filter,
        ).count()

        msg_qs = Message.objects.filter(deleted_at__isnull=True)
        if user_id is not None:
            msg_qs = msg_qs.filter(author_id=user_id)
        msg_count = msg_qs.count()

        return {
            'total_messages': msg_count,
            'active_conversations': conv_count,
        }
