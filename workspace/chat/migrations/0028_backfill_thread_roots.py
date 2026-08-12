from django.db import migrations

from workspace.chat.services.threads import backfill_threads


def forwards(apps, schema_editor):
    backfill_threads(
        apps.get_model("chat", "Message"),
        apps.get_model("chat", "ThreadParticipant"),
        apps.get_model("chat", "ConversationMember"),
    )


def backwards(apps, schema_editor):
    message_model = apps.get_model("chat", "Message")
    message_model.objects.filter(thread_root__isnull=False).update(thread_root=None)
    message_model.objects.filter(reply_count__gt=0).update(
        reply_count=0, last_reply_at=None
    )
    apps.get_model("chat", "ThreadParticipant").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0027_threadparticipant_message_last_reply_at_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
