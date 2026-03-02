from datetime import date

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate

from workspace.core.activity_registry import ActivityProvider


class FilesActivityProvider(ActivityProvider):

    def _visibility_filter(self, user_id, viewer_id):
        """Return Q filter restricting to files visible to viewer."""
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
        from workspace.files.models import File

        qs = File.objects.filter(
            deleted_at__isnull=True,
            node_type=File.NodeType.FILE,
            updated_at__date__gte=date_from,
            updated_at__date__lte=date_to,
        )
        if user_id is not None:
            qs = qs.filter(owner_id=user_id)
        qs = qs.filter(self._visibility_filter(user_id, viewer_id))

        rows = qs.annotate(day=TruncDate('updated_at')).values('day').annotate(
            count=Count('pk'),
        ).order_by('day')

        return {row['day']: row['count'] for row in rows}

    def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
        from workspace.files.models import File

        qs = File.objects.filter(
            deleted_at__isnull=True,
            node_type=File.NodeType.FILE,
        )
        if user_id is not None:
            qs = qs.filter(owner_id=user_id)
        qs = qs.filter(
            self._visibility_filter(user_id, viewer_id),
        ).select_related('owner').order_by('-updated_at')[offset:offset + limit]

        events = []
        for f in qs:
            is_new = abs((f.created_at - f.updated_at).total_seconds()) < 2
            events.append({
                'icon': 'hard-drive',
                'label': 'File created' if is_new else 'File updated',
                'description': f.name,
                'timestamp': f.updated_at,
                'url': f'/files?preview={f.pk}',
                'actor': {
                    'id': f.owner_id,
                    'username': f.owner.username,
                    'full_name': f.owner.get_full_name(),
                },
            })
        return events

    def get_stats(self, user_id, *, viewer_id=None):
        from workspace.files.models import File

        qs = File.objects.filter(
            deleted_at__isnull=True,
            node_type=File.NodeType.FILE,
        )
        if user_id is not None:
            qs = qs.filter(owner_id=user_id)
        qs = qs.filter(self._visibility_filter(user_id, viewer_id))

        agg = qs.aggregate(
            total_files=Count('pk'),
            total_size=Sum('size'),
        )
        return {
            'total_files': agg['total_files'] or 0,
            'total_size': agg['total_size'] or 0,
        }
