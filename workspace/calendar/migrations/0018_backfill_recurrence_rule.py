"""Fill the new recurrence columns from the three legacy ones.

The legacy vocabulary (FREQ + INTERVAL + an end date) is a strict subset of
RRULE, so this direction is lossless. The reverse is not: BYDAY, BYSETPOS,
COUNT and RDATE have nowhere to go in the old columns, which is the entire
reason for the change.
"""

from datetime import UTC

from django.db import migrations

_FREQ = {
    "daily": "DAILY",
    "weekly": "WEEKLY",
    "monthly": "MONTHLY",
    "yearly": "YEARLY",
}


def rule_for(frequency, interval, recurrence_end):
    """Rule text equivalent to a legacy row's three columns.

    Deliberately does not call services.recurrence_rule.from_simple. A
    migration is a historical record: importing live app code makes an
    already-applied migration change meaning, or break outright, the next time
    that code is edited. The duplication is the point.
    """
    if not frequency:
        return ""
    parts = [f"FREQ={_FREQ[frequency]}"]
    if interval and int(interval) > 1:
        parts.append(f"INTERVAL={int(interval)}")
    if recurrence_end:
        # UNTIL is a UTC instant; the column is aware but not guaranteed to
        # already be in UTC once a caller has localized it.
        parts.append(
            f"UNTIL={recurrence_end.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        )
    return "RRULE:" + ";".join(parts)


def until_for(recurrence_end, duration):
    """Legacy end date as an end-of-last-occurrence bound."""
    if recurrence_end is None:
        return None
    return recurrence_end + duration if duration else recurrence_end


def forwards(apps, schema_editor):
    Event = apps.get_model("calendar", "Event")
    # Route every query at the connection being migrated. Without this the
    # ORM uses the DEFAULT database, which is a different one whenever the
    # target is not the default - migrate_to_postgres being the case that
    # matters. There the default SQLite source is already at HEAD, so a
    # bare Event.objects looks for columns 0019 has dropped.
    db = schema_editor.connection.alias
    updates = []
    fields = ("recurrence_rule", "is_recurring", "recurrence_until")
    for event in Event.objects.using(db).exclude(recurrence_frequency=None).iterator():
        duration = (event.end - event.start) if event.end else None
        event.recurrence_rule = rule_for(
            event.recurrence_frequency, event.recurrence_interval, event.recurrence_end
        )
        event.is_recurring = True
        event.recurrence_until = until_for(event.recurrence_end, duration)
        updates.append(event)
        if len(updates) >= 500:
            Event.objects.using(db).bulk_update(updates, fields)
            updates.clear()
    if updates:
        Event.objects.using(db).bulk_update(updates, fields)


def backwards(apps, schema_editor):
    """Blank the new columns.

    Deliberately not a true inverse: a rule the old columns cannot express is
    unrecoverable, so pretending otherwise would corrupt data on a rollback.

    Nothing is recovered either. Reaching this migration backwards means 0019
    has already been reversed, and that re-creates the three legacy columns
    from their field defaults (NULL / 1 / NULL) rather than from the values
    they held before 0019 dropped them. A rollback past this point therefore
    loses every event's recurrence outright: dump the table first.
    """
    Event = apps.get_model("calendar", "Event")
    db = schema_editor.connection.alias
    Event.objects.using(db).update(
        recurrence_rule="", is_recurring=False, recurrence_until=None
    )


class Migration(migrations.Migration):
    dependencies = [("calendar", "0017_event_recurrence_rule")]
    operations = [migrations.RunPython(forwards, backwards)]
