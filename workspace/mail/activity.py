from datetime import date

from django.db.models import Count, Q
from django.db.models.functions import TruncDate

from workspace.core.activity_registry import ActivityProvider


class MailActivityProvider(ActivityProvider):

    def _base_filter(self, user_id):
        """Return Q filter for mail queries.

        When *user_id* is set (profile / heatmap) we only count **sent**
        mail — it represents the user's own activity.
        When *user_id* is ``None`` (dashboard / workspace feed) we only
        show **received** (inbox) mail — things that happened to people.
        """
        if user_id is not None:
            return Q(account__owner_id=user_id, folder__folder_type='sent')
        return Q(folder__folder_type='inbox')

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        from workspace.mail.models import MailMessage

        qs = MailMessage.objects.filter(
            deleted_at__isnull=True,
            date__date__gte=date_from,
            date__date__lte=date_to,
        ).filter(self._base_filter(user_id))

        rows = qs.annotate(day=TruncDate('date')).values('day').annotate(
            count=Count('pk'),
        ).order_by('day')

        return {row['day']: row['count'] for row in rows}

    def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
        from workspace.mail.models import MailMessage

        is_sent = user_id is not None

        qs = MailMessage.objects.filter(
            deleted_at__isnull=True,
        ).filter(
            self._base_filter(user_id),
        ).select_related(
            'account__owner',
        ).order_by('-date')[offset:offset + limit]

        events = []
        for msg in qs:
            if is_sent:
                actor_name = msg.account.owner.get_full_name()
            else:
                actor_name = ''
                if msg.from_address:
                    actor_name = msg.from_address.get('name') or msg.from_address.get('address', '')
                actor_name = actor_name or msg.account.owner.get_full_name()

            events.append({
                'icon': 'mail',
                'label': 'Email sent' if is_sent else 'Email received',
                'description': msg.subject or '(no subject)',
                'timestamp': msg.date or msg.created_at,
                'url': f'/mail/{msg.account_id}/messages/{msg.pk}',
                'actor': {
                    'id': msg.account.owner_id,
                    'username': msg.account.owner.username,
                    'full_name': actor_name,
                },
            })
        return events

    def get_stats(self, user_id, *, viewer_id=None):
        from workspace.mail.models import MailAccount, MailMessage

        msg_qs = MailMessage.objects.filter(deleted_at__isnull=True)
        if user_id is not None:
            msg_qs = msg_qs.filter(account__owner_id=user_id)

        total_messages = msg_qs.count()
        unread_messages = msg_qs.filter(is_read=False).count()

        acct_qs = MailAccount.objects.filter(is_active=True)
        if user_id is not None:
            acct_qs = acct_qs.filter(owner_id=user_id)
        total_accounts = acct_qs.count()

        return {
            'total_messages': total_messages,
            'unread_messages': unread_messages,
            'total_accounts': total_accounts,
        }
