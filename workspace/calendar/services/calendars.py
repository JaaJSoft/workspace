"""Creating and changing a calendar, external ones included.

An external calendar is a calendar fed by an ICS feed someone else publishes:
the same row, plus the ``ExternalCalendar`` that remembers the feed and where
its sync stands.
"""

from django.db import transaction

from workspace.common.task_priority import INTERACTIVE_PRIORITY

from ..models import Calendar
from ..models_external import ExternalCalendar
from ..tasks import sync_external_calendar_task


def external_source(calendar):
    """The feed *calendar* is filled from, or None for one written by hand."""
    return getattr(calendar, "external_source", None)


def _send_sync(source):
    sync_external_calendar_task.apply_async(
        [str(source.uuid)], priority=INTERACTIVE_PRIORITY
    )


@transaction.atomic
def create_calendar(owner, *, name, color, source_url=None):
    """A calendar of *owner*, filled from *source_url* when one is given."""
    calendar = Calendar.objects.create(owner=owner, name=name, color=color)
    if source_url:
        source = ExternalCalendar.objects.create(calendar=calendar, url=source_url)
        # After the commit: a worker starting earlier finds no such calendar.
        # Robust: the periodic sync picks up a feed whose first sync was not sent.
        transaction.on_commit(lambda: _send_sync(source), robust=True)
    return calendar


def update_calendar(calendar, changes):
    """Write *changes* (name, color) to *calendar*."""
    for field, value in changes.items():
        setattr(calendar, field, value)
    # Every change to one of its events bumps sync_revision on the same row.
    calendar.save(update_fields=[*changes, "updated_at"])
    return calendar


def request_sync(calendar):
    """Ask for the feed of an external *calendar* to be read again now."""
    _send_sync(calendar.external_source)
