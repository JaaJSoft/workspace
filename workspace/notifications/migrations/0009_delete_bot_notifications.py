from django.db import migrations


def delete_bot_notifications(apps, schema_editor):
    """Drop notification rows addressed to bot accounts.

    Bots were ordinary conversation members, so every message sent to one
    created a row for it. Nothing ever reads or marks those rows read, and
    the pruning task only deletes read rows, so they accumulated forever.
    """
    Notification = apps.get_model("notifications", "Notification")
    BotProfile = apps.get_model("ai", "BotProfile")
    db = schema_editor.connection.alias

    bot_user_ids = BotProfile.objects.using(db).values_list("user_id", flat=True)
    Notification.objects.using(db).filter(recipient_id__in=bot_user_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0008_backfill_chat_source"),
        ("ai", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(delete_bot_notifications, migrations.RunPython.noop),
    ]
