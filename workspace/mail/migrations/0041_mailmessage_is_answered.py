from django.db import migrations, models


def read_every_flag_again(apps, schema_editor):
    """Make the next sync read the flags of every folder in full.

    The incremental pass only reads the flags that changed since the last
    one, so a message answered before this field existed would stay
    unanswered until the daily full read.
    """
    MailFolder = apps.get_model("mail", "MailFolder")
    db = schema_editor.connection.alias
    MailFolder.objects.using(db).update(remote_state=None)


class Migration(migrations.Migration):
    dependencies = [
        ("mail", "0040_mailaccount_archives_in_all_mail"),
    ]

    operations = [
        migrations.AddField(
            model_name="mailmessage",
            name="is_answered",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(read_every_flag_again, migrations.RunPython.noop),
    ]
