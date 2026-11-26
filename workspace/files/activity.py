from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils.translation import gettext

from workspace.common.datetimes import local_date_range
from workspace.core.activity_registry import ActivityProvider, UsageHeadline


class FileFeedActivityProvider(ActivityProvider):
    """Activity of one half of the file event stream.

    Notes (markdown files) form one half and every other file the other, so a
    note edit is never counted twice. A subclass picks its half with
    ``notes``, names its stat with ``stat_key`` and says where an event opens.
    """

    notes = False
    stat_key = ""

    def event_url(self, file):
        raise NotImplementedError

    def _in_feed(self, qs, prefix=""):
        """Narrow *qs* to this provider's half; *prefix* is the path to the File."""
        markdown = {f"{prefix}mime_type": "text/markdown"}
        return qs.filter(**markdown) if self.notes else qs.exclude(**markdown)

    def _viewer_accessible_file_ids(self, viewer_id):
        """IDs of files the viewer can access (owned + group + shared).

        Event access follows file access, so visibility is derived from the
        centralized ``accessible_file_ids`` rather than a local
        reimplementation. That helper does not filter ``deleted_at``, so
        events on trashed files stay reachable for users who can see them.
        """
        from django.contrib.auth import get_user_model

        from workspace.files.queries import accessible_file_ids

        viewer = get_user_model().objects.get(pk=viewer_id)
        return accessible_file_ids(viewer)

    @staticmethod
    def _is_own_view(user_id, viewer_id):
        """Whether *user_id* is looking at their own activity: callers pass no
        viewer for it, or the user themselves."""
        return user_id is not None and viewer_id in (None, user_id)

    def _file_visibility_filter(self, user_id, viewer_id):
        """Return a Q filter on the File model restricting to files visible to viewer.

        The owner's own view is no exception: the group nodes they created in
        a group they have left are no longer theirs to see.
        """
        from workspace.files.queries import owned_files_q

        if self._is_own_view(user_id, viewer_id):
            return owned_files_q(user_id)
        if viewer_id is None:
            return Q()
        return Q(pk__in=self._viewer_accessible_file_ids(viewer_id))

    def _event_visibility_filter(self, user_id, viewer_id):
        """Return a Q filter on FileEvent restricting to events on files visible to viewer."""
        from workspace.files.queries import owned_files_qs

        if self._is_own_view(user_id, viewer_id):
            return Q(file__in=owned_files_qs(user_id))
        if viewer_id is None:
            return Q()
        return Q(file_id__in=self._viewer_accessible_file_ids(viewer_id))

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        from workspace.files.models import File, FileEvent

        start, end = local_date_range(date_from, date_to)
        # Events on soft-deleted files are kept in the feed: hiding them
        # would also hide the DELETED event itself (the file is in trash
        # by the time that event lands), and aligns this provider with the
        # per-file panel (events_for_file) and the REST endpoint, neither
        # of which filter on file__deleted_at.
        qs = FileEvent.objects.filter(
            file__node_type=File.NodeType.FILE,
            created_at__gte=start,
            created_at__lt=end,
        )
        qs = self._in_feed(qs, prefix="file__")
        if user_id is not None:
            qs = qs.filter(file__owner_id=user_id)
        qs = qs.filter(self._event_visibility_filter(user_id, viewer_id))

        rows = (
            qs.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("pk"))
            .order_by("day")
        )
        return {row["day"]: row["count"] for row in rows}

    def get_recent_events(
        self, user_id, limit=10, offset=0, *, viewer_id=None, exclude_actor_id=None
    ):
        from django.contrib.auth import get_user_model

        from workspace.files.models import FILE_FEED_Q, NOTE_FEED_Q
        from workspace.files.queries import owned_files_q
        from workspace.files.services.events import recent_feed_events

        # See get_daily_counts: events are not filtered by file__deleted_at
        # so the DELETED event itself is reachable from the feed and the
        # provider stays consistent with the per-file timeline.
        feed_q = NOTE_FEED_Q if self.notes else FILE_FEED_Q
        viewer = None
        if self._is_own_view(user_id, viewer_id):
            feed_q &= owned_files_q(user_id)
        elif viewer_id is not None:
            viewer = get_user_model().objects.get(pk=viewer_id)
        feed = recent_feed_events(
            feed_q,
            owner_id=user_id,
            viewer=viewer,
            exclude_actor_id=exclude_actor_id,
            limit=limit,
            offset=offset,
        )

        events = []
        for ev in feed:
            # Events without an actor are system-driven (Celery cleanup,
            # sync soft-delete, ...). Reporting them as the file owner
            # would falsely attribute the action to a real user; emit a
            # null actor instead - the dashboard template already hides
            # the actor block when it is missing.
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
                    "url": self.event_url(ev.file),
                    "actor": actor_data,
                }
            )
        return events

    def get_stats(self, user_id, *, viewer_id=None):
        from workspace.files.models import File

        qs = self._in_feed(
            File.objects.filter(deleted_at__isnull=True, node_type=File.NodeType.FILE)
        )
        if user_id is not None:
            qs = qs.filter(owner_id=user_id)
        qs = qs.filter(self._file_visibility_filter(user_id, viewer_id))

        return {self.stat_key: qs.count()}


class FilesActivityProvider(FileFeedActivityProvider):
    stat_key = "total_files"

    def event_url(self, file):
        # The file's parent folder with the viewer opened, or the files root
        # for a top-level file.
        if file.parent_id:
            return f"/files/{file.parent_id}?open={file.pk}"
        return f"/files?open={file.pk}"

    def get_headline(self, stats):
        return UsageHeadline(stats["total_files"], gettext("Files"))
