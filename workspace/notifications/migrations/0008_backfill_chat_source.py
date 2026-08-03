import uuid

from django.db import migrations


def _parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError, TypeError:
        return None


def backfill_chat_conversations(apps, schema_editor):
    """Populate conversation_id on unread chat rows from their /chat/<uuid> url.

    Defensive by design: unparseable urls and urls pointing at since-deleted
    conversations are left FK-less rather than failing the deploy.
    """
    Notification = apps.get_model("notifications", "Notification")
    Conversation = apps.get_model("chat", "Conversation")

    conv_ids = set(Conversation.objects.values_list("uuid", flat=True))
    to_update = []
    for notif in Notification.objects.filter(
        origin="chat", read_at__isnull=True, conversation__isnull=True
    ).exclude(url=""):
        if not notif.url.startswith("/chat/"):
            continue
        conv_uuid = _parse_uuid(notif.url.removeprefix("/chat/"))
        if conv_uuid is None or conv_uuid not in conv_ids:
            continue
        notif.conversation_id = conv_uuid
        to_update.append(notif)
    if to_update:
        Notification.objects.bulk_update(to_update, ["conversation"])


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0007_notification_source_fks"),
        ("chat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_chat_conversations, migrations.RunPython.noop),
    ]
