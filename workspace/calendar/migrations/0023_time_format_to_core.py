"""Carry the calendar's 12h clock preference over to the app-wide core setting.

The calendar kept its own ``timeFormat`` in its preferences object; the core
``time_format`` setting now applies to every module. 24h is the default of
both, so only the users who picked 12h have anything to carry over. The
leftover key in the calendar object is ignored by its schema.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    UserSetting = apps.get_model("users", "UserSetting")
    db = schema_editor.connection.alias
    twelve_hour = {
        user_id
        for user_id, value in UserSetting.objects.using(db)
        .filter(module="calendar", key="preferences")
        .values_list("user_id", "value")
        if isinstance(value, dict) and value.get("timeFormat") == "12h"
    }
    already_set = set(
        UserSetting.objects.using(db)
        .filter(module="core", key="time_format", user_id__in=twelve_hour)
        .values_list("user_id", flat=True)
    )
    UserSetting.objects.using(db).bulk_create(
        UserSetting(user_id=user_id, module="core", key="time_format", value="12h")
        for user_id in twelve_hour - already_set
    )


class Migration(migrations.Migration):
    dependencies = [
        ("calendar", "0022_event_search_splits_punctuation"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
