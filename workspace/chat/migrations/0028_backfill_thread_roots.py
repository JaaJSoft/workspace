from django.db import migrations

# A live-code import inside a migration: backfill_threads only touches the
# historical models passed in below, but moving or renaming it would break
# `migrate` from scratch. If services.threads ever loses that function, freeze
# a copy of it in this file instead of updating the import.
from workspace.chat.services.threads import backfill_threads


def forwards(apps, schema_editor):
    backfill_threads(
        apps.get_model("chat", "Message"),
        apps.get_model("chat", "ThreadParticipant"),
        apps.get_model("chat", "ConversationMember"),
        schema_editor.connection.alias,
    )


def backwards(apps, schema_editor):
    message_model = apps.get_model("chat", "Message")
    db = schema_editor.connection.alias
    message_model.objects.using(db).filter(thread_root__isnull=False).update(
        thread_root=None
    )
    message_model.objects.using(db).filter(reply_count__gt=0).update(
        reply_count=0, last_reply_at=None
    )
    apps.get_model("chat", "ThreadParticipant").objects.using(db).all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0027_threadparticipant_message_last_reply_at_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
