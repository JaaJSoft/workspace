from django.db.models import Count, Q
from django.db.models.functions import TruncDate

from workspace.core.activity_registry import ActivityProvider

# Notes are markdown files; their activity is the FileEvent stream filtered
# to that mime type. The files provider excludes the same mime type, so the
# two feeds partition the event stream and a note edit is never counted twice.
_NOTE_MIME = "text/markdown"


class NotesActivityProvider(ActivityProvider):
    def _base_qs(self, user_id):
        """Live notes (markdown File rows) owned by *user_id* - used for stats."""
        from workspace.files.models import File

        qs = File.objects.filter(
            deleted_at__isnull=True,
            node_type=File.NodeType.FILE,
            mime_type=_NOTE_MIME,
        )
        if user_id is not None:
            qs = qs.filter(owner_id=user_id)
        return qs

    def _stats_visibility_filter(self, user_id, viewer_id):
        """Restrict the File-level stats queryset to notes visible to viewer."""
        if viewer_id is None or viewer_id == user_id:
            return Q()
        from workspace.files.models import FileShare

        q = Q(owner_id=viewer_id)
        share_filter = {"shared_with_id": viewer_id}
        if user_id is not None:
            share_filter["file__owner_id"] = user_id
        shared_file_ids = FileShare.objects.filter(
            **share_filter,
        ).values_list("file_id", flat=True)
        return q | Q(pk__in=shared_file_ids)

    def _event_visibility_filter(self, user_id, viewer_id):
        """Restrict a FileEvent queryset to events on notes visible to viewer."""
        if viewer_id is None or viewer_id == user_id:
            return Q()
        from workspace.files.models import FileShare

        q = Q(file__owner_id=viewer_id)
        share_filter = {"shared_with_id": viewer_id}
        if user_id is not None:
            share_filter["file__owner_id"] = user_id
        shared_file_ids = FileShare.objects.filter(
            **share_filter,
        ).values_list("file_id", flat=True)
        return q | Q(file_id__in=shared_file_ids)

    def _events_qs(self, user_id, viewer_id):
        from workspace.files.models import File, FileEvent

        # Events on soft-deleted notes are kept (mirrors the files provider):
        # hiding them would also hide the Trashed event itself.
        qs = FileEvent.objects.filter(
            file__node_type=File.NodeType.FILE,
            file__mime_type=_NOTE_MIME,
        )
        if user_id is not None:
            qs = qs.filter(file__owner_id=user_id)
        return qs.filter(self._event_visibility_filter(user_id, viewer_id))

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        qs = self._events_qs(user_id, viewer_id).filter(
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        rows = (
            qs.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("pk"))
            .order_by("day")
        )
        return {row["day"]: row["count"] for row in rows}

    def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
        qs = (
            self._events_qs(user_id, viewer_id)
            .select_related("actor", "file")
            .order_by("-created_at")[offset : offset + limit]
        )

        events = []
        for ev in qs:
            # System-driven events (Celery cleanup, sync soft-delete) have no
            # actor; emit a null actor rather than falsely crediting the owner.
            if ev.actor is not None:
                actor_data = {
                    "id": ev.actor.pk,
                    "username": ev.actor.username,
                    "full_name": ev.actor.get_full_name(),
                }
            else:
                actor_data = None
            events.append(
                {
                    "icon": ev.icon,
                    "label": ev.short_label,
                    "description": ev.file.name,
                    "timestamp": ev.created_at,
                    "url": f"/notes?file={ev.file.pk}",
                    "actor": actor_data,
                }
            )
        return events

    def get_stats(self, user_id, *, viewer_id=None):
        count = (
            self._base_qs(user_id)
            .filter(self._stats_visibility_filter(user_id, viewer_id))
            .count()
        )
        return {"total_notes": count}
