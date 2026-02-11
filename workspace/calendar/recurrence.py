from datetime import timedelta

from dateutil.rrule import rrule, DAILY, WEEKLY, MONTHLY, YEARLY

from workspace.users.avatar_service import has_avatar

FREQ_MAP = {
    'daily': DAILY,
    'weekly': WEEKLY,
    'monthly': MONTHLY,
    'yearly': YEARLY,
}


def _build_rrule(master, range_start, range_end):
    """Yield occurrence start datetimes for a recurring master event."""
    freq = FREQ_MAP.get(master.recurrence_frequency)
    if freq is None:
        return

    until = range_end
    if master.recurrence_end and master.recurrence_end < until:
        until = master.recurrence_end

    rule = rrule(
        freq,
        interval=master.recurrence_interval,
        dtstart=master.start,
        until=until,
    )

    for dt in rule:
        if dt >= range_end:
            break
        # Compute occurrence end to check overlap with range
        if master.end:
            duration = master.end - master.start
            occ_end = dt + duration
        else:
            occ_end = dt

        # Only yield if the occurrence overlaps the query range
        if master.end:
            if occ_end > range_start:
                yield dt
        else:
            if dt >= range_start:
                yield dt


def _user_dict(user):
    avatar_url = f'/api/v1/users/{user.id}/avatar' if has_avatar(user) else None
    return {
        'id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'avatar_url': avatar_url,
    }


def _member_dict(member):
    return {
        'uuid': str(member.uuid),
        'user': _user_dict(member.user),
        'status': member.status,
        'created_at': member.created_at.isoformat(),
    }


def make_virtual_occurrence(master, occ_start):
    """Build a dict for a virtual (non-materialized) occurrence."""
    duration = (master.end - master.start) if master.end else None
    occ_end = (occ_start + duration) if duration else None

    return {
        'uuid': f'{master.uuid}:{occ_start.isoformat()}',
        'calendar_id': str(master.calendar_id),
        'title': master.title,
        'description': master.description,
        'start': occ_start.isoformat(),
        'end': occ_end.isoformat() if occ_end else None,
        'all_day': master.all_day,
        'location': master.location,
        'owner': _user_dict(master.owner),
        'members': [_member_dict(m) for m in master.members.all()],
        'created_at': master.created_at.isoformat(),
        'updated_at': master.updated_at.isoformat(),
        'is_recurring': True,
        'is_exception': False,
        'master_event_id': str(master.uuid),
        'original_start': occ_start.isoformat(),
        'recurrence_frequency': master.recurrence_frequency,
        'recurrence_interval': master.recurrence_interval,
        'recurrence_end': master.recurrence_end.isoformat() if master.recurrence_end else None,
    }


def make_exception_dict(exc):
    """Convert a materialized exception Event to the occurrence dict format."""
    return {
        'uuid': str(exc.uuid),
        'calendar_id': str(exc.calendar_id),
        'title': exc.title,
        'description': exc.description,
        'start': exc.start.isoformat(),
        'end': exc.end.isoformat() if exc.end else None,
        'all_day': exc.all_day,
        'location': exc.location,
        'owner': _user_dict(exc.owner),
        'members': [_member_dict(m) for m in exc.members.all()],
        'created_at': exc.created_at.isoformat(),
        'updated_at': exc.updated_at.isoformat(),
        'is_recurring': True,
        'is_exception': True,
        'master_event_id': str(exc.recurrence_parent_id),
        'original_start': exc.original_start.isoformat() if exc.original_start else None,
        'recurrence_frequency': exc.recurrence_parent.recurrence_frequency if exc.recurrence_parent else None,
        'recurrence_interval': exc.recurrence_parent.recurrence_interval if exc.recurrence_parent else 1,
        'recurrence_end': exc.recurrence_parent.recurrence_end.isoformat() if exc.recurrence_parent and exc.recurrence_parent.recurrence_end else None,
    }


def expand_recurring_events(masters_qs, range_start, range_end):
    """
    Expand recurring master events into occurrence dicts.
    Substitutes materialized exceptions, skips cancelled ones.
    """
    from .models import Event, EventMember
    from django.db.models import Prefetch

    master_ids = [m.uuid for m in masters_qs]
    if not master_ids:
        return []

    # Fetch all exceptions for these masters, prefetch members
    exceptions = (
        Event.objects.filter(recurrence_parent_id__in=master_ids)
        .select_related('owner', 'calendar', 'recurrence_parent')
        .prefetch_related(
            Prefetch('members', queryset=EventMember.objects.select_related('user'))
        )
    )

    # Index by (parent_id, original_start)
    exc_index = {}
    for exc in exceptions:
        if exc.original_start:
            key = (str(exc.recurrence_parent_id), exc.original_start.isoformat())
            exc_index[key] = exc

    occurrences = []
    for master in masters_qs:
        for occ_start in _build_rrule(master, range_start, range_end):
            key = (str(master.uuid), occ_start.isoformat())
            exc = exc_index.get(key)
            if exc:
                if exc.is_cancelled:
                    continue  # Skip cancelled occurrences
                occurrences.append(make_exception_dict(exc))
            else:
                occurrences.append(make_virtual_occurrence(master, occ_start))

    return occurrences
