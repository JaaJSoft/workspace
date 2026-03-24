from datetime import date

from django.db.models import Count, Q
from django.db.models.functions import TruncDate

from workspace.core.activity_registry import ActivityProvider


class NotesActivityProvider(ActivityProvider):

    def _base_qs(self, user_id):
        from workspace.files.models import File

        qs = File.objects.filter(
            deleted_at__isnull=True,
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )
        if user_id is not None:
            qs = qs.filter(owner_id=user_id)
        return qs

    def _visibility_filter(self, user_id, viewer_id):
        """Restrict to notes visible to viewer (owned or shared)."""
        if viewer_id is None or viewer_id == user_id:
            return Q()
        from workspace.files.models import FileShare
        q = Q(owner_id=viewer_id)
        share_filter = {'shared_with_id': viewer_id}
        if user_id is not None:
            share_filter['file__owner_id'] = user_id
        shared_file_ids = FileShare.objects.filter(
            **share_filter,
        ).values_list('file_id', flat=True)
        return q | Q(pk__in=shared_file_ids)

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        qs = self._base_qs(user_id).filter(
            self._visibility_filter(user_id, viewer_id),
            updated_at__date__gte=date_from,
            updated_at__date__lte=date_to,
        )
        rows = qs.annotate(day=TruncDate('updated_at')).values('day').annotate(
            count=Count('pk'),
        ).order_by('day')
        return {row['day']: row['count'] for row in rows}

    def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
        qs = self._base_qs(user_id).filter(
            self._visibility_filter(user_id, viewer_id),
        ).select_related('owner').order_by(
            '-updated_at',
        )[offset:offset + limit]

        events = []
        for f in qs:
            is_new = abs((f.created_at - f.updated_at).total_seconds()) < 2
            events.append({
                'icon': 'notebook-pen',
                'label': 'Note created' if is_new else 'Note updated',
                'description': f.name,
                'timestamp': f.updated_at,
                'url': f'/notes?file={f.uuid}',
                'actor': {
                    'id': f.owner_id,
                    'username': f.owner.username,
                    'full_name': f.owner.get_full_name(),
                },
            })
        return events

    def get_stats(self, user_id, *, viewer_id=None):
        count = self._base_qs(user_id).filter(
            self._visibility_filter(user_id, viewer_id),
        ).count()
        return {'total_notes': count}
