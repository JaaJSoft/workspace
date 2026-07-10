"""Backfill the flat address columns from the JSON fields.

Runs before 0024 removes ``from_address``: the sender dict is copied into
``from_email`` / ``from_name`` and the to/cc lists are flattened into
``recipients_text``. The derivation is inlined (not imported from the app)
because it reads the historical ``from_address`` field, which no longer
exists in application code.

Batched so the write transaction stays short on SQLite (single writer):
each chunk of 1000 rows is read, derived in Python and bulk_updated.
"""

from itertools import batched

from django.db import migrations

BATCH_SIZE = 1000
COLUMNS = ["from_email", "from_name", "recipients_text"]


def _flatten_entry(entry):
    if not isinstance(entry, dict):
        return ""
    name = (entry.get("name") or "").strip()
    email = (entry.get("email") or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return email or name


def backfill(apps, schema_editor):
    MailMessage = apps.get_model("mail", "MailMessage")
    qs = (
        MailMessage.objects.using(schema_editor.connection.alias)
        .only("uuid", "from_address", "to_addresses", "cc_addresses")
        .iterator(chunk_size=BATCH_SIZE)
    )
    for chunk in batched(qs, BATCH_SIZE, strict=False):
        for msg in chunk:
            sender = msg.from_address if isinstance(msg.from_address, dict) else {}
            msg.from_email = (sender.get("email") or "").strip()[:254]
            msg.from_name = (sender.get("name") or "").strip()[:255]
            parts = []
            for field in (msg.to_addresses, msg.cc_addresses):
                if isinstance(field, list):
                    parts.extend(
                        filter(None, (_flatten_entry(entry) for entry in field))
                    )
            msg.recipients_text = ", ".join(parts)
        MailMessage.objects.using(schema_editor.connection.alias).bulk_update(
            chunk, COLUMNS
        )


class Migration(migrations.Migration):
    # Commit per batch instead of one long transaction: keeps the SQLite
    # write lock short, and the backfill is idempotent if interrupted.
    atomic = False

    dependencies = [
        ("mail", "0022_mailmessage_from_email_mailmessage_from_name_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
