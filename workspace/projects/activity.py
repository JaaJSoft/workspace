from django.db.models import Count, Q
from django.db.models.functions import TruncDate

from workspace.common.datetimes import local_date_range
from workspace.core.activity_registry import ActivityProvider


class ProjectsActivityProvider(ActivityProvider):
    @staticmethod
    def _scoping_user(user_id, viewer_id):
        """The user whose access scopes the feed: the viewer, or the user
        themselves on their own view (callers pass no viewer for it). None
        only for an unscoped, system-wide query.

        The own view is no exception: a project the user was removed from is
        no longer theirs to see, under its current name or otherwise.
        """
        from django.contrib.auth import get_user_model

        scoping_id = user_id if viewer_id is None else viewer_id
        if scoping_id is None:
            return None
        # The access helpers only read the pk: an unsaved stub spares a user
        # fetch on every provider call (the dashboard makes several).
        return get_user_model()(pk=scoping_id)

    def _event_visibility_filter(self, user_id, viewer_id):
        """Q filter on TaskEvent restricting to the events the scoping user may read."""
        from workspace.projects.queries import visible_task_events_q

        scoping_user = self._scoping_user(user_id, viewer_id)
        if scoping_user is None:
            return Q()
        return visible_task_events_q(scoping_user)

    def _task_visibility_filter(self, user_id, viewer_id):
        """Q filter on Task restricting to projects the scoping user can access."""
        from workspace.projects.queries import user_project_ids

        scoping_user = self._scoping_user(user_id, viewer_id)
        if scoping_user is None:
            return Q()
        return Q(project_id__in=user_project_ids(scoping_user))

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        from workspace.projects.models import TaskEvent

        start, end = local_date_range(date_from, date_to)
        qs = TaskEvent.objects.filter(
            created_at__gte=start,
            created_at__lt=end,
        )
        if user_id is not None:
            qs = qs.filter(actor_id=user_id)
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
        from workspace.projects.models import TaskEvent

        qs = TaskEvent.objects.all()
        if user_id is not None:
            qs = qs.filter(actor_id=user_id)
        if exclude_actor_id is not None:
            qs = qs.exclude(actor_id=exclude_actor_id)
        qs = (
            qs.filter(self._event_visibility_filter(user_id, viewer_id))
            .select_related("actor", "project")
            .order_by("-created_at")[offset : offset + limit]
        )
        from workspace.projects.services.events import serialize_task_event

        events = []
        for ev in qs:
            event = serialize_task_event(ev)
            # Project name gives cross-project feeds (dashboard, profile)
            # their context; the in-panel feed hides meta and doesn't need it.
            event["meta_icon"] = "square-kanban"
            event["meta_text"] = ev.project.name
            events.append(event)
        return events

    def get_stats(self, user_id, *, viewer_id=None):
        from workspace.projects.models import Task

        qs = Task.objects.all()
        if user_id is not None:
            qs = qs.filter(created_by_id=user_id)
        qs = qs.filter(self._task_visibility_filter(user_id, viewer_id))
        agg = qs.aggregate(
            total_tasks=Count("pk"),
            completed_tasks=Count("pk", filter=Q(completed_at__isnull=False)),
        )
        return {
            "total_tasks": agg["total_tasks"] or 0,
            "completed_tasks": agg["completed_tasks"] or 0,
        }
