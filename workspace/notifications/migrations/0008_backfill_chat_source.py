import uuid
from itertools import batched

from django.db import migrations

BATCH_SIZE = 1000


def _parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError, TypeError:
        return None


def backfill_chat_conversations(apps, schema_editor):
    """Populate conversation_id on unread chat rows from their /chat/<uuid> url.

    Defensive by design: unparseable urls and urls pointing at since-deleted
    conversations are left FK-less rather than failing the deploy. Rows are
    streamed and flushed in bounded batches so memory stays flat regardless
    of table size.
    """
    Notification = apps.get_model("notifications", "Notification")
    Conversation = apps.get_model("chat", "Conversation")
    db = schema_editor.connection.alias

    qs = (
        Notification.objects.using(db).filter(
            origin="chat", read_at__isnull=True, conversation__isnull=True
        )
        .exclude(url="")
        .only("uuid", "url")
    )
    for batch in batched(qs.iterator(chunk_size=BATCH_SIZE), BATCH_SIZE, strict=False):
        candidates = []
        for notif in batch:
            if not notif.url.startswith("/chat/"):
                continue
            conv_uuid = _parse_uuid(notif.url.removeprefix("/chat/"))
            if conv_uuid is None:
                continue
            notif.conversation_id = conv_uuid
            candidates.append(notif)
        if not candidates:
            continue
        live = set(
            Conversation.objects.using(db).filter(
                uuid__in={n.conversation_id for n in candidates}
            ).values_list("uuid", flat=True)
        )
        to_update = [n for n in candidates if n.conversation_id in live]
        if to_update:
            Notification.objects.using(db).bulk_update(to_update, ["conversation"])


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0007_notification_source_fks"),
        ("chat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_chat_conversations, migrations.RunPython.noop),
    ]
