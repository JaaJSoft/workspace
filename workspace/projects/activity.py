from django.db.models import Count, Q
from django.db.models.functions import TruncDate

from workspace.core.activity_registry import ActivityProvider


class ProjectsActivityProvider(ActivityProvider):
    def _visibility_filter(self, user_id, viewer_id):
        """Q filter on TaskEvent restricting to projects the viewer can access."""
        if viewer_id is None or viewer_id == user_id:
            return Q()
        return Q(project_id__in=self._viewer_project_ids(viewer_id))

    def _viewer_project_ids(self, viewer_id):
        from django.contrib.auth import get_user_model

        from workspace.projects.queries import user_project_ids

        viewer = get_user_model().objects.get(pk=viewer_id)
        return user_project_ids(viewer)

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        from workspace.projects.models import TaskEvent

        qs = TaskEvent.objects.filter(
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        if user_id is not None:
            qs = qs.filter(actor_id=user_id)
        qs = qs.filter(self._visibility_filter(user_id, viewer_id))
        rows = (
            qs.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("pk"))
            .order_by("day")
        )
        return {row["day"]: row["count"] for row in rows}

    def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
        from workspace.projects.models import TaskEvent

        qs = TaskEvent.objects.all()
        if user_id is not None:
            qs = qs.filter(actor_id=user_id)
        qs = (
            qs.filter(self._visibility_filter(user_id, viewer_id))
            .select_related("actor")
            .order_by("-created_at")[offset : offset + limit]
        )
        events = []
        for ev in qs:
            # Null actor means a system-driven write; never attribute it to
            # a real user (same convention as the files provider).
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
                    "description": ev.task_title,
                    "timestamp": ev.created_at,
                    "url": f"/projects/{ev.project_id}",
                    "actor": actor_data,
                }
            )
        return events

    def get_stats(self, user_id, *, viewer_id=None):
        from workspace.projects.models import Task

        qs = Task.objects.all()
        if user_id is not None:
            qs = qs.filter(created_by_id=user_id)
        qs = qs.filter(self._visibility_filter(user_id, viewer_id))
        agg = qs.aggregate(
            total_tasks=Count("pk"),
            completed_tasks=Count("pk", filter=Q(completed_at__isnull=False)),
        )
        return {
            "total_tasks": agg["total_tasks"] or 0,
            "completed_tasks": agg["completed_tasks"] or 0,
        }
