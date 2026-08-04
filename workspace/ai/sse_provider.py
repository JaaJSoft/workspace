from workspace.ai.models import AITask
from workspace.core.sse_registry import SSEProvider


class AIStreamSSEProvider(SSEProvider):
    """Streams ephemeral bot progress steps. No DB queries - cache only.

    Events carry no SSE id on purpose: Last-Event-Id is shared by all
    providers on the stream and the chat provider resolves it as a message
    UUID for replay; steps are fire-and-forget and need no replay.
    """

    def __init__(self, user, last_event_id):
        super().__init__(user, last_event_id)
        from workspace.ai.services.stream_steps import latest_step_id

        # Steps queued before this connection belong to a generation it did
        # not witness; replaying them would raise a phantom typing bubble on
        # a page load. Start from the tail and only stream what comes next.
        self._cursor = latest_step_id(user.id)

    def get_initial_events(self):
        return []

    def poll(self, cache_value):
        from workspace.ai.services.stream_steps import read_steps

        envelopes, self._cursor = read_steps(self.user.id, self._cursor)
        return [("bot_step", envelope["data"], None) for envelope in envelopes]


class AISSEProvider(SSEProvider):
    """SSE provider for AI task completion notifications."""

    def get_initial_events(self):
        tasks = AITask.objects.filter(
            owner=self.user,
            status__in=[AITask.Status.PENDING, AITask.Status.PROCESSING],
        ).values("uuid", "task_type", "status")

        if not tasks:
            return []

        return [
            (
                "ai_tasks",
                {
                    "tasks": [
                        {
                            "uuid": str(t["uuid"]),
                            "task_type": t["task_type"],
                            "status": t["status"],
                        }
                        for t in tasks
                    ],
                },
                None,
            )
        ]

    def poll(self, cache_value):
        # Only query when notify_sse('ai', user_id) was called
        if cache_value is None:
            return []

        from datetime import timedelta

        from django.utils import timezone

        cutoff = timezone.now() - timedelta(seconds=30)
        tasks = AITask.objects.filter(
            owner=self.user,
            status__in=[AITask.Status.COMPLETED, AITask.Status.FAILED],
            completed_at__gte=cutoff,
        ).values("uuid", "task_type", "status", "result", "error")

        events = []
        for t in tasks:
            events.append(
                (
                    "ai_task_complete",
                    {
                        "uuid": str(t["uuid"]),
                        "task_type": t["task_type"],
                        "status": t["status"],
                        "result": t["result"],
                        "error": t["error"],
                    },
                    str(t["uuid"]),
                )
            )

        return events
