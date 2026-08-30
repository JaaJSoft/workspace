"""Scope-aware editing and cancellation of calendar events.

A recurring series is stored as one master row plus materialized exception
rows, so "change this one", "change this one and the following ones" and
"change the whole series" are three different writes. The REST views and the
AI tools both need all three, and re-deriving occurrence splitting on either
side is how the two drift apart, so the whole decision tree lives here.

``scope`` is always explicit: there is no default, because guessing ``all``
turns "skip next Monday" into "delete the weekly meeting".
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction

from workspace.notifications.services.notifications import notify_many

from ..models import Calendar, Event, EventMember
from ..models_external import ExternalCalendar
from .recurrence_rule import apply_rule, truncate_before
from .timezones import current_timezone_name, normalize_all_day

User = get_user_model()

# Fields a caller may set directly on an event row.
EDITABLE_FIELDS = (
    "title",
    "description",
    "start",
    "end",
    "all_day",
    "location",
    "recurrence_rule",
)


class EventScopeError(Exception):
    """An edit that cannot be satisfied — bad scope, missing occurrence, no access.

    ``status_code`` is the HTTP status the REST layer should answer with;
    tool callers only read ``detail``.
    """

    def __init__(self, detail, status_code=400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def is_external_calendar(calendar_id):
    """True when the calendar is fed by an ICS subscription.

    Writes to those are pointless: the next ``sync_external_calendar`` run
    reverts them, and the caller has already reported success.
    """
    return ExternalCalendar.objects.filter(calendar_id=calendar_id).exists()


def assert_writable(event, user):
    """Raise unless *user* may write *event*."""
    if event.owner_id != user.id:
        raise EventScopeError("Only the owner can edit this event.", 403)
    if is_external_calendar(event.calendar_id):
        raise EventScopeError(
            "This event comes from an external calendar and cannot be edited.", 403
        )


def _truncate_series(master, cut):
    """Stop *master* before *cut*, rewriting the rule and its derived bound.

    The rule text is authoritative, so a split has to edit it: moving only the
    derived bound would leave clients importing a series that never ends.
    """
    apply_rule(
        master, truncate_before(master.recurrence_rule, cut - timedelta(seconds=1))
    )
    master.save(
        update_fields=[
            "recurrence_rule",
            "is_recurring",
            "recurrence_until",
            "recurrence_frequency",
            "recurrence_interval",
            "recurrence_end",
        ]
    )


def _apply_fields(event, data, user):
    """Apply common field updates to an event."""
    if "calendar_id" in data:
        try:
            event.calendar = Calendar.objects.get(pk=data["calendar_id"], owner=user)
        except Calendar.DoesNotExist:
            raise EventScopeError("Calendar not found.") from None

    had_recurrence = event.is_recurring
    for field in EDITABLE_FIELDS:
        if field in data:
            setattr(event, field, data[field])
    if event.all_day:
        # Enforce the storage invariant even when all_day was already set
        # and only start/end changed.
        event.start = normalize_all_day(event.start)
        event.end = normalize_all_day(event.end)
        event.timezone = ""
    elif event.recurrence_rule and not had_recurrence and not event.timezone:
        # Checked against the raw field, not is_recurring: apply_rule (which
        # recomputes is_recurring) runs after this block, so is_recurring
        # still reflects the pre-edit state here. Only a series GAINING
        # recurrence anchors its wall clock in the editing zone. Legacy
        # recurring series (blank timezone) must keep UTC expansion:
        # stamping them on an unrelated edit would shift future occurrences
        # and orphan their exceptions.
        event.timezone = current_timezone_name()
    # One call, after every field has settled: the bound is derived from the
    # rule AND from start/end/timezone, any of which the loop above may have
    # moved. The plain setattr in the loop is what this call then normalizes.
    apply_rule(event, event.recurrence_rule)
    event.save()


@transaction.atomic
def sync_members(event, member_ids, owner_id):
    """Sync event members from a list of user IDs.

    Returns the set of user IDs that were added or removed (already notified
    separately, so the caller must not notify them twice).
    """
    current = set(event.members.values_list("user_id", flat=True))
    new_ids = set(member_ids) - {owner_id}
    to_remove = current - new_ids
    if to_remove:
        removed_users = list(User.objects.filter(id__in=to_remove))
        EventMember.objects.filter(event=event, user_id__in=to_remove).delete()
        if removed_users:
            notify_many(
                recipients=removed_users,
                origin="calendar",
                title=f'Removed from "{event.title}"',
                body=f"{event.owner.username} removed you from an event.",
                url=f"/calendar?event={event.pk}",
                actor=event.owner,
                source=event,
            )
    to_add = new_ids - current
    if to_add:
        users = list(User.objects.filter(id__in=to_add))
        EventMember.objects.bulk_create(
            [EventMember(event=event, user=u) for u in users]
        )
        notify_many(
            recipients=users,
            origin="calendar",
            title=f'Invited to "{event.title}"',
            body=f"{event.owner.username} invited you to an event.",
            url=f"/calendar?event={event.pk}",
            actor=event.owner,
            source=event,
        )
    return to_add | to_remove


def _copy_members(source, target, data, user):
    """Give *target* the members named in *data*, or inherit *source*'s."""
    if "member_ids" not in data:
        EventMember.objects.bulk_create(
            [
                EventMember(event=target, user=m.user, status=m.status)
                for m in source.members.all()
            ]
        )
        return

    member_ids = set(data["member_ids"]) - {user.id}
    existing_ids = set(source.members.values_list("user_id", flat=True))
    users = list(User.objects.filter(id__in=member_ids))
    EventMember.objects.bulk_create([EventMember(event=target, user=u) for u in users])
    new_users = [u for u in users if u.id not in existing_ids]
    if new_users:
        notify_many(
            recipients=new_users,
            origin="calendar",
            title=f'Invited to "{target.title}"',
            body=f"{user.username} invited you to an event.",
            url=f"/calendar?event={target.pk}",
            actor=user,
            source=target,
        )


def _inherited_end(master, start):
    """End of an occurrence starting at *start*, keeping the series' duration.

    Anchoring on *start* rather than on the occurrence's original slot is what
    keeps a moved occurrence coherent: a caller that passes a new start and no
    end would otherwise get the old slot's end, i.e. an event that finishes
    before it begins.
    """
    if not master.end:
        return None
    return start + (master.end - master.start)


def _derived_times(master, data, original_start):
    """Return ``(start, end, all_day)`` for a row split off *master*.

    The all-day invariant is enforced here for the same reason
    ``_apply_fields`` enforces it on the update path: ``all_day`` can be
    inherited from the master while ``start`` comes from the caller, so a
    payload that never mentions ``all_day`` can still land a non-midnight
    day label that the rest of the module assumes cannot exist.
    """
    start = data.get("start", original_start)
    end = data.get("end", _inherited_end(master, start))
    all_day = data.get("all_day", master.all_day)
    if all_day:
        start = normalize_all_day(start)
        end = normalize_all_day(end)
    return start, end, all_day


def _require_original_start(original_start, scope):
    if not original_start:
        raise EventScopeError(f"original_start is required for scope={scope}.")


def update_event(event, data, user, *, scope, original_start=None):
    """Apply *data* to *event* under *scope*, and return the row that was written.

    That row is the event itself for ``all``, the materialized exception for
    ``this``, and the newly split master for ``future`` — never the same
    object for two different scopes, which is why callers re-read it rather
    than assuming they still hold the target.
    """
    if not event.is_recurring or scope == "all":
        return _update_whole_event(event, data, user)
    if scope == "this":
        return _update_single_occurrence(event, data, user, original_start)
    if scope == "future":
        return _update_future_occurrences(event, data, user, original_start)
    raise EventScopeError("Invalid scope.")


def _update_whole_event(event, data, user):
    _apply_fields(event, data, user)

    changed_ids = set()
    if "member_ids" in data:
        changed_ids = sync_members(event, data["member_ids"], user.id)

    # Notify remaining members about the update, excluding the editor and
    # the users sync_members already told about their add/removal.
    exclude_ids = changed_ids | {user.id}
    member_users = list(
        User.objects.filter(calendar_invitations__event=event).exclude(
            id__in=exclude_ids
        )
    )
    if member_users:
        notify_many(
            recipients=member_users,
            origin="calendar",
            title=f'"{event.title}" was updated',
            body=f"{user.username} updated an event you are part of.",
            url=f"/calendar?event={event.pk}",
            actor=user,
            source=event,
        )
    return event


def _update_single_occurrence(master, data, user, original_start):
    """Materialize (or update) the exception row for one occurrence."""
    _require_original_start(original_start, "this")

    exc = Event.objects.filter(
        recurrence_parent=master, original_start=original_start
    ).first()
    if exc:
        _apply_fields(exc, data, user)
        if "member_ids" in data:
            sync_members(exc, data["member_ids"], user.id)
        return exc

    start, end, all_day = _derived_times(master, data, original_start)
    with transaction.atomic():
        exc = Event.objects.create(
            calendar=master.calendar,
            title=data.get("title", master.title),
            description=data.get("description", master.description),
            start=start,
            end=end,
            all_day=all_day,
            location=data.get("location", master.location),
            owner=master.owner,
            recurrence_parent=master,
            original_start=original_start,
        )
        _copy_members(master, exc, data, user)
    return exc


@transaction.atomic
def _update_future_occurrences(master, data, user, original_start):
    """Split the series: truncate the old master, start a new one at *original_start*."""
    _require_original_start(original_start, "future")

    # Captured before truncation: the new master continues the series past
    # the split, so it must inherit the rule as it stood before the OLD
    # master got its UNTIL rewritten, not the now-truncated text.
    original_rule = master.recurrence_rule

    _truncate_series(master, original_start)

    Event.objects.filter(
        recurrence_parent=master, original_start__gte=original_start
    ).delete()

    start, end, all_day = _derived_times(master, data, original_start)
    new_master = Event(
        calendar=master.calendar,
        title=data.get("title", master.title),
        description=data.get("description", master.description),
        start=start,
        end=end,
        all_day=all_day,
        location=data.get("location", master.location),
        owner=master.owner,
        # Without the series' zone the split half falls back to legacy
        # fixed-step UTC expansion, shifting every later occurrence by an
        # hour across a DST boundary.
        timezone="" if all_day else master.timezone,
    )
    apply_rule(new_master, data.get("recurrence_rule", original_rule))
    new_master.save()

    if "calendar_id" in data:
        try:
            new_master.calendar = Calendar.objects.get(
                pk=data["calendar_id"], owner=user
            )
            new_master.save(update_fields=["calendar_id"])
        except Calendar.DoesNotExist:
            # Unknown calendar_id or calendar owned by someone else: keep the
            # master's calendar instead of failing the split.
            pass

    _copy_members(master, new_master, data, user)
    return new_master


@transaction.atomic
def cancel_event(event, user, *, scope, original_start=None):
    """Delete or cancel *event* under *scope*.

    ``all`` deletes the row (and its exceptions, by cascade), ``this``
    materializes a cancelled exception so the occurrence disappears from the
    expansion, and ``future`` truncates the series just before
    *original_start*.
    """
    if not event.is_recurring or scope == "all":
        member_users = list(
            User.objects.filter(calendar_invitations__event=event).exclude(id=user.id)
        )
        if member_users:
            notify_many(
                recipients=member_users,
                origin="calendar",
                title=f'"{event.title}" was cancelled',
                body=f"{user.username} cancelled an event you were part of.",
                actor=user,
            )
        event.delete()
        return

    if scope not in ("this", "future"):
        raise EventScopeError("Invalid scope.")

    _require_original_start(original_start, scope)

    if scope == "this":
        exc = Event.objects.filter(
            recurrence_parent=event, original_start=original_start
        ).first()
        if exc:
            exc.is_cancelled = True
            exc.save(update_fields=["is_cancelled"])
        else:
            Event.objects.create(
                calendar=event.calendar,
                title=event.title,
                start=original_start,
                owner=event.owner,
                recurrence_parent=event,
                original_start=original_start,
                is_cancelled=True,
            )
        return

    _truncate_series(event, original_start)
    Event.objects.filter(
        recurrence_parent=event, original_start__gte=original_start
    ).delete()
