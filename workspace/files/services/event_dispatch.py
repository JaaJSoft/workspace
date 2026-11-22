"""In-process registry that runs handlers when a FileEvent is recorded.

Handlers subscribe to one or more FileEvent.Action values via @on_file_event.
record_event() (services/events.py) schedules them on transaction commit, so
they run after the mutation is durably committed: off the request thread
through the files.run_file_event_handlers Celery task, or in place for an
event of a bulk job, which already runs in a worker.

A handler is called as ``handler(event, priority)``. *priority* is the level
the tasks it queues go out at - normal for someone's action, low for a bulk
job - and a handler queueing one passes it to ``apply_async``.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError

from workspace.common.task_priority import NORMAL_PRIORITY

logger = logging.getLogger(__name__)

# action value (str) -> list of handler callables taking a FileEvent
_HANDLERS: dict[str, list] = {}


def on_file_event(*actions):
    """Register the decorated function as a handler for the given actions."""

    def decorator(fn):
        for action in actions:
            _HANDLERS.setdefault(str(action), []).append(fn)
        return fn

    return decorator


def has_handlers(action) -> bool:
    """True if at least one handler is registered for *action*."""
    return bool(_HANDLERS.get(str(action)))


def run_handlers(event_uuid) -> None:
    """Load the FileEvent and run every handler registered for its action."""
    from ..models import FileEvent

    try:
        event = FileEvent.objects.select_related("file", "actor").get(uuid=event_uuid)
    except FileEvent.DoesNotExist, ValidationError, ValueError, TypeError:
        # Unknown or malformed event id (e.g. a non-UUID string): nothing to run.
        return
    run_event_handlers(event, NORMAL_PRIORITY)


def run_event_handlers(event, priority) -> None:
    """Run every handler registered for *event*'s action, at *priority*.

    Handlers are best-effort side effects: one raising is logged and does not
    stop the others or propagate.
    """
    for handler in _HANDLERS.get(str(event.action), []):
        try:
            handler(event, priority)
        except Exception:
            logger.exception(
                "File event handler %s failed for event %s (%s)",
                getattr(handler, "__name__", handler),
                event.uuid,
                event.action,
            )
