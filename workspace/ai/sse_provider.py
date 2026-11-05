import time

from workspace.ai.models import AITask
from workspace.core.sse_registry import SSEProvider

# How often a connection re-announces the generations still under way. The
# chat page drops its typing bubble when nothing has kept it alive for a few
# minutes (a cancelled generation ends without posting anything), so this has
# to be well under that failsafe, or a reply that takes long to compose loses
# its bubble while the model is still working.
GENERATING_HEARTBEAT_SECONDS = 60


class AIStreamSSEProvider(SSEProvider):
    """Streams ephemeral bot progress steps.

    ``poll`` reads the cache, so waking this provider on every tool
    execution stays cheap; it queries the database only once per
    ``GENERATING_HEARTBEAT_SECONDS``, to re-announce the generations still
    under way. ``get_initial_events`` queries once per connection, to hand a
    fresh page the state it could not have witnessed.

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
        self._announced_generating = set()
        self._last_generating_check = time.monotonic()

    def _conversations_generating(self):
        """Ids of the user's conversations with a bot response under way.

        The chat AITask is owned by the bot, not by the human reading this
        stream, so ownership cannot be the filter here - membership is.
        """
        from workspace.ai.services.chat_generations import active_chat_tasks
        from workspace.chat.services.conversations import user_conversation_ids

        candidates = set()
        for data in active_chat_tasks().values_list("input_data", flat=True):
            conversation_id = (data or {}).get("conversation_id")
            if conversation_id:
                candidates.add(str(conversation_id))
        if not candidates:
            return set()

        mine = user_conversation_ids(self.user).filter(conversation_id__in=candidates)
        return {str(uuid) for uuid in mine}

    def get_initial_events(self):
        """Hand a fresh connection the generations already under way.

        A page load lands with no memory of what came before it, so without
        this the bubble stays down until the next tool starts - a minute away
        on an image. Announcing the running conversations is also what makes
        replaying their queued steps safe: steps left over from a generation
        that has since finished are skipped, so a reload never resurrects a
        bubble for work that is already done.

        The snapshot is sent even when nothing is running: a reconnecting
        client (mobile resume opens a brand-new EventSource, so the chat
        provider replays nothing) may still be showing a bubble raised
        before the stream dropped, and the empty set is what tells it the
        generation ended while it was away.
        """
        from workspace.ai.services.stream_steps import read_steps

        running = self._conversations_generating()
        events = [self._generating_event(running)]
        if running:
            envelopes, self._cursor = read_steps(self.user.id, None)
            events.extend(
                ("bot_step", envelope["data"], None)
                for envelope in envelopes
                if envelope["data"].get("conversation_id") in running
            )
        return events

    def _generating_event(self, running):
        self._announced_generating = running
        self._last_generating_check = time.monotonic()
        return ("bot_generating", {"conversation_ids": sorted(running)}, None)

    def _generating_heartbeat(self):
        """The snapshot again, while a generation runs or right after it ends.

        A tool step is the only other thing that keeps the bubble up, and a
        model can compose for minutes after its last tool - or call none at
        all. Once nothing runs and the client was told so, the heartbeat goes
        quiet: an empty snapshot repeated every minute would only cost a
        query and a frame per idle tab.
        """
        if (
            time.monotonic() - self._last_generating_check
            < GENERATING_HEARTBEAT_SECONDS
        ):
            return []
        running = self._conversations_generating()
        if not running and not self._announced_generating:
            self._last_generating_check = time.monotonic()
            return []
        return [self._generating_event(running)]

    def poll(self, cache_value):
        from workspace.ai.services.stream_steps import read_steps

        events = self._generating_heartbeat()
        envelopes, self._cursor = read_steps(self.user.id, self._cursor)
        events.extend(("bot_step", envelope["data"], None) for envelope in envelopes)
        return events


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
