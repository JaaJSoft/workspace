from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate

from workspace.core.activity_registry import ActivityProvider


class FilesActivityProvider(ActivityProvider):
    def _viewer_accessible_file_ids(self, viewer_id):
        """IDs of files the viewer can access (owned + group + shared).

        Event access follows file access, so visibility is derived from the
        centralized ``FileService.accessible_files_q`` rather than a local
        reimplementation. That helper does not filter ``deleted_at``, so
        events on trashed files stay reachable for users who can see them.
        """
        from django.contrib.auth import get_user_model

        from workspace.files.models import File
        from workspace.files.services import FileService

        viewer = get_user_model().objects.get(pk=viewer_id)
        return File.objects.filter(
            FileService.accessible_files_q(viewer),
        ).values_list("pk", flat=True)

    def _file_visibility_filter(self, user_id, viewer_id):
        """Return a Q filter on the File model restricting to files visible to viewer."""
        if viewer_id is None or viewer_id == user_id:
            return Q()
        return Q(pk__in=self._viewer_accessible_file_ids(viewer_id))

    def _event_visibility_filter(self, user_id, viewer_id):
        """Return a Q filter on FileEvent restricting to events on files visible to viewer."""
        if viewer_id is None or viewer_id == user_id:
            return Q()
        return Q(file_id__in=self._viewer_accessible_file_ids(viewer_id))

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        from workspace.files.models import File, FileEvent

        # Events on soft-deleted files are kept in the feed: hiding them
        # would also hide the DELETED event itself (the file is in trash
        # by the time that event lands), and aligns this provider with the
        # per-file panel (events_for_file) and the REST endpoint, neither
        # of which filter on file__deleted_at.
        # Markdown notes are surfaced by the notes provider; excluding them
        # here keeps the two feeds disjoint so a note edit isn't counted twice.
        qs = FileEvent.objects.filter(
            file__node_type=File.NodeType.FILE,
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        ).exclude(file__mime_type="text/markdown")
        if user_id is not None:
            qs = qs.filter(file__owner_id=user_id)
        qs = qs.filter(self._event_visibility_filter(user_id, viewer_id))

        rows = (
            qs.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(
                count=Count("pk"),
            )
            .order_by("day")
        )

        return {row["day"]: row["count"] for row in rows}

    def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
        from workspace.files.models import File, FileEvent

        # See get_daily_counts: events are not filtered by file__deleted_at
        # so the DELETED event itself is reachable from the feed and the
        # provider stays consistent with the per-file timeline.
        qs = FileEvent.objects.filter(
            file__node_type=File.NodeType.FILE,
        ).exclude(file__mime_type="text/markdown")
        if user_id is not None:
            qs = qs.filter(file__owner_id=user_id)
        qs = (
            qs.filter(
                self._event_visibility_filter(user_id, viewer_id),
            )
            .select_related("actor", "file")
            .order_by("-created_at")[offset : offset + limit]
        )

        events = []
        for ev in qs:
            # Events without an actor are system-driven (Celery cleanup,
            # sync soft-delete, ...). Reporting them as the file owner
            # would falsely attribute the action to a real user; emit a
            # null actor instead - the dashboard template already hides
            # the actor block when it's missing.
            if ev.actor is not None:
                actor_data = {
                    "id": ev.actor.pk,
                    "username": ev.actor.username,
                    "full_name": ev.actor.get_full_name(),
                }
            else:
                actor_data = None
            # Mirror the notes module's "Open in Files": land in the file's
            # parent folder (/files/<folder>) with the viewer opened (?open=),
            # falling back to the files root for top-level files.
            parent_id = ev.file.parent_id
            url = (
                f"/files/{parent_id}?open={ev.file.pk}"
                if parent_id
                else f"/files?open={ev.file.pk}"
            )
            events.append(
                {
                    "icon": ev.icon,
                    "label": ev.short_label,
                    "description": ev.file.name,
                    "timestamp": ev.created_at,
                    "url": url,
                    "actor": actor_data,
                }
            )
        return events

    def get_stats(self, user_id, *, viewer_id=None):
        from workspace.files.models import File

        # Notes are counted by the notes provider's total_notes stat; keep
        # them out of total_files so a note isn't tallied in both cards.
        qs = File.objects.filter(
            deleted_at__isnull=True,
            node_type=File.NodeType.FILE,
        ).exclude(mime_type="text/markdown")
        if user_id is not None:
            qs = qs.filter(owner_id=user_id)
        qs = qs.filter(self._file_visibility_filter(user_id, viewer_id))

        agg = qs.aggregate(
            total_files=Count("pk"),
            total_size=Sum("size"),
        )
        return {
            "total_files": agg["total_files"] or 0,
            "total_size": agg["total_size"] or 0,
        }
