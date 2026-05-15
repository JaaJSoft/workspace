from django.db import migrations, models


def backfill_source(apps, schema_editor):
    Event = apps.get_model('calendar', 'Event')
    # Anything with a non-empty ical_uid was created from an ICS attachment.
    Event.objects.filter(ical_uid__isnull=False).exclude(ical_uid='').update(source='ics')


class Migration(migrations.Migration):

    dependencies = [
        ('calendar', '0013_event_unique_calendar_ical_uid'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='source',
            field=models.CharField(
                choices=[('manual', 'Manual'), ('ics', 'ICS invitation'), ('llm', 'Extracted by AI')],
                default='manual', max_length=16,
            ),
        ),
        migrations.RunPython(backfill_source, migrations.RunPython.noop),
    ]
