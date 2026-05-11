from django.db import migrations, models
from django.db.models import Count


def dedupe_event_ical_uid(apps, schema_editor):
    """Collapse duplicate ``(calendar, ical_uid)`` rows before the
    partial UniqueConstraint is added.

    Keeps the oldest row (by ``created_at``) and deletes the rest. Cascades
    on ``Event`` clean up child rows (``EventMember``, exception events,
    ``Poll.event=SET_NULL``, ``source_message=SET_NULL``) — losing them on
    the duplicate copies is the desired behaviour since they should never
    have existed in the first place.
    """
    Event = apps.get_model('calendar', 'Event')
    duplicates = (
        Event.objects
        .exclude(ical_uid__isnull=True)
        .exclude(ical_uid='')
        .values('calendar_id', 'ical_uid')
        .annotate(c=Count('uuid'))
        .filter(c__gt=1)
    )
    for group in duplicates:
        rows = list(
            Event.objects
            .filter(calendar_id=group['calendar_id'], ical_uid=group['ical_uid'])
            .order_by('created_at', 'uuid')
            .values_list('uuid', flat=True)
        )
        # Keep the oldest, drop the rest.
        Event.objects.filter(uuid__in=rows[1:]).delete()


def noop_reverse(apps, schema_editor):
    """Dedupe is irreversible — restoring deleted duplicates is impossible."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('calendar', '0012_drop_redundant_indexes'),
    ]

    operations = [
        migrations.RunPython(dedupe_event_ical_uid, noop_reverse),
        migrations.AddConstraint(
            model_name='event',
            constraint=models.UniqueConstraint(
                fields=['calendar', 'ical_uid'],
                condition=models.Q(ical_uid__isnull=False) & ~models.Q(ical_uid=''),
                name='unique_event_ical_uid_per_calendar',
            ),
        ),
    ]
