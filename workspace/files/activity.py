from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate

from workspace.core.activity_registry import ActivityProvider


class FilesActivityProvider(ActivityProvider):

    def _file_visibility_filter(self, user_id, viewer_id):
        """Return a Q filter on the File model restricting to files visible to viewer."""
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

    def _event_visibility_filter(self, user_id, viewer_id):
        """Return a Q filter on FileEvent restricting to events on files visible to viewer."""
        if viewer_id is None or viewer_id == user_id:
            return Q()
        from workspace.files.models import FileShare
        q = Q(file__owner_id=viewer_id)
        share_filter = {'shared_with_id': viewer_id}
        if user_id is not None:
            share_filter['file__owner_id'] = user_id
        shared_file_ids = FileShare.objects.filter(
            **share_filter,
        ).values_list('file_id', flat=True)
        return q | Q(file_id__in=shared_file_ids)

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        from workspace.files.models import File, FileEvent

        qs = FileEvent.objects.filter(
            file__node_type=File.NodeType.FILE,
            file__deleted_at__isnull=True,
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        if user_id is not None:
            qs = qs.filter(file__owner_id=user_id)
        qs = qs.filter(self._event_visibility_filter(user_id, viewer_id))

        rows = qs.annotate(day=TruncDate('created_at')).values('day').annotate(
            count=Count('pk'),
        ).order_by('day')

        return {row['day']: row['count'] for row in rows}

    def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
        from workspace.files.models import File, FileEvent

        qs = FileEvent.objects.filter(
            file__node_type=File.NodeType.FILE,
            file__deleted_at__isnull=True,
        )
        if user_id is not None:
            qs = qs.filter(file__owner_id=user_id)
        qs = qs.filter(
            self._event_visibility_filter(user_id, viewer_id),
        ).select_related('actor', 'file', 'file__owner').order_by('-created_at')[offset:offset + limit]

        events = []
        for ev in qs:
            # Fall back to the file owner when an event has no actor (e.g.
            # system-generated events from a Celery task).
            actor = ev.actor or ev.file.owner
            events.append({
                'icon': ev.icon,
                'label': ev.short_label,
                'description': ev.file.name,
                'timestamp': ev.created_at,
                'url': f'/files?preview={ev.file.pk}',
                'actor': {
                    'id': actor.pk,
                    'username': actor.username,
                    'full_name': actor.get_full_name(),
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
        qs = qs.filter(self._file_visibility_filter(user_id, viewer_id))

        agg = qs.aggregate(
            total_files=Count('pk'),
            total_size=Sum('size'),
        )
        return {
            'total_files': agg['total_files'] or 0,
            'total_size': agg['total_size'] or 0,
        }
