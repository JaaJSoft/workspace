"""Change tracking behind CalDAV sync tokens and ETags.

Every write to an event bumps its calendar's ``sync_revision`` and stamps the
object it belongs to (the series master, for an exception row) with that
revision in ``CalendarObjectChange``. A sync token is a past revision: what
changed since is every row stamped after it. An object's ETag is derived from
its own stamp, so it moves exactly when the object does.

The hooks are model signals rather than calls in each writer because events
are written from a dozen places (REST views, AI tools, invitations, feed
sync, CalDAV itself) and a writer that forgot to call in would leave phones
showing stale events with nothing to tell anyone. The flip side: a queryset
``update()`` sends no signal, so it must not touch a field CalDAV serves.
``calendar_recheck_recurrence`` is the one bulk writer, and it only rewrites
the derived index columns, which are never rendered.
"""

from django.db.models import F, QuerySet
from django.db.models.signals import post_delete, post_init, post_save
from django.dispatch import receiver

from ..models import Calendar, CalendarObjectChange, Event

# Part of every ETag: bump it when the rendering of an event changes, so
# clients refetch objects whose rows did not move.
RENDER_VERSION = 1


def object_name(event):
    """The CalDAV resource name of a series master or single event."""
    return event.dav_name or f"{event.uuid}.ics"


def object_etag(event_uuid, revision):
    return f'"{event_uuid.hex[:12]}-{revision}-{RENDER_VERSION}"'


def object_revisions(calendar_id, event_uuids=None):
    """``{event_uuid: revision}`` for the objects of a calendar that have one.

    An object with no row has not changed since change tracking began, and
    is at revision 0.
    """
    rows = CalendarObjectChange.objects.filter(calendar_id=calendar_id)
    if event_uuids is not None:
        rows = rows.filter(event_uuid__in=event_uuids)
    return dict(rows.values_list("event_uuid", "revision"))


def changes_since(calendar_id, revision):
    """The change rows stamped after *revision*, oldest first."""
    return CalendarObjectChange.objects.filter(
        calendar_id=calendar_id, revision__gt=revision
    ).order_by("revision")


def record_change(calendar_id, event_uuid, name):
    """Stamp one object of *calendar_id* with a fresh revision.

    Returns the revision, or None when the calendar is gone.
    """
    if not Calendar.objects.filter(pk=calendar_id).update(
        sync_revision=F("sync_revision") + 1
    ):
        return None
    revision = (
        Calendar.objects.filter(pk=calendar_id)
        .values_list("sync_revision", flat=True)
        .get()
    )
    CalendarObjectChange.objects.update_or_create(
        calendar_id=calendar_id,
        event_uuid=event_uuid,
        defaults={"name": name, "revision": revision},
    )
    return revision


def _touch_master(master_id):
    """Stamp the object an exception row belongs to, if its master still exists."""
    master = (
        Event.objects.filter(pk=master_id)
        .values_list("calendar_id", "dav_name")
        .first()
    )
    if master is None:
        return
    calendar_id, dav_name = master
    record_change(calendar_id, master_id, dav_name or f"{master_id}.ics")


def _loaded_calendar_id(instance):
    # Read from __dict__: a deferred calendar_id must not cost a query here.
    return instance.__dict__.get("calendar_id")


@receiver(post_init, sender=Event)
def _remember_calendar(sender, instance, **kwargs):
    instance._sync_calendar_id = _loaded_calendar_id(instance)


@receiver(post_save, sender=Event)
def _on_event_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    if instance.recurrence_parent_id:
        _touch_master(instance.recurrence_parent_id)
    else:
        previous = getattr(instance, "_sync_calendar_id", None)
        if not created and previous and previous != instance.calendar_id:
            # Moved: the old calendar must report the object as gone.
            record_change(previous, instance.uuid, object_name(instance))
        record_change(instance.calendar_id, instance.uuid, object_name(instance))
    instance._sync_calendar_id = instance.calendar_id


def _deleted_on_its_own(origin):
    """True when the deletion started from events, not from their calendar.

    Deleting a calendar (or its owner) cascades to its events; stamping a
    calendar that is about to disappear would insert change rows the cascade
    has already collected past, and fail on the foreign key at commit.
    """
    if isinstance(origin, Event):
        return True
    return isinstance(origin, QuerySet) and origin.model is Event


@receiver(post_delete, sender=Event)
def _on_event_deleted(sender, instance, origin=None, **kwargs):
    if not _deleted_on_its_own(origin):
        return
    if instance.recurrence_parent_id:
        # Gone already when the whole series is deleted at once: the master's
        # own signal reports it.
        _touch_master(instance.recurrence_parent_id)
    else:
        record_change(instance.calendar_id, instance.uuid, object_name(instance))
