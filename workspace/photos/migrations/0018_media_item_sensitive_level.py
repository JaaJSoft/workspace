from django.db import migrations, models

# files.sensitivity.MAX_LEVEL when this migration was written: the level of a
# photo blurred under every preference but off.
MAX_LEVEL = 4


def forwards(apps, schema_editor):
    """A sensitive photo starts at the top level, a choice made by hand
    included. The catch-up then moves the detector's verdicts to the level
    their stored score gives, without running the model again.

    A user who turned the blur off keeps it off, as the ``off`` preference.
    """
    MediaItem = apps.get_model("photos", "MediaItem")
    UserSetting = apps.get_model("users", "UserSetting")
    db = schema_editor.connection.alias
    MediaItem.objects.using(db).filter(sensitive=True).update(sensitive_level=MAX_LEVEL)
    old = UserSetting.objects.using(db).filter(module="core", key="blur_sensitive")
    turned_off = [row.user_id for row in old.iterator() if row.value is False]
    UserSetting.objects.using(db).bulk_create(
        [
            UserSetting(user_id=user_id, module="core", key="sensitive_blur", value="off")
            for user_id in turned_off
        ],
        ignore_conflicts=True,
    )
    old.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("photos", "0017_media_item_sensitive"),
        ("users", "0010_oidcidentity_synced_groups"),
    ]

    operations = [
        migrations.AddField(
            model_name="mediaitem",
            name="sensitive_level",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="mediaitem",
            name="sensitive",
        ),
    ]
