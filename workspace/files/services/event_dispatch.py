"""In-process registry that runs handlers when a FileEvent is recorded.

Handlers subscribe to one or more FileEvent.Action values via @on_file_event.
record_event() (services/events.py) schedules run_handlers() on transaction
commit, so handlers run after the mutation is durably committed and off the
request thread (via the files.run_file_event_handlers Celery task).
"""

from __future__ import annotations

import logging

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
    """Load the FileEvent and run every handler registered for its action.

    Handlers are best-effort side effects: one raising is logged and does not
    stop the others or propagate.
    """
    from workspace.files.models import FileEvent

    try:
        event = FileEvent.objects.select_related("file", "actor").get(uuid=event_uuid)
    except FileEvent.DoesNotExist:
        return

    for handler in _HANDLERS.get(str(event.action), []):
        try:
            handler(event)
        except Exception:
            logger.exception(
                "File event handler %s failed for event %s (%s)",
                getattr(handler, "__name__", handler),
                event.uuid,
                event.action,
            )
