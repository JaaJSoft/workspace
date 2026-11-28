from email.policy import default as default_policy
from email.utils import getaddresses
from itertools import batched

from django.db import migrations, models


def _parse(header):
    """The ``[{name, email}, ...]`` of a stored Reply-To header.

    Inlined rather than imported from `services/imap_parse`: a migration must
    keep its meaning when the live parser changes.
    """
    try:
        parsed = default_policy.header_factory("Reply-To", header)
        entries = [
            (
                address.display_name,
                address.addr_spec if address.domain else address.username,
            )
            for address in parsed.addresses
        ]
    except Exception:
        entries = getaddresses([header])
    return [{"name": name, "email": email} for name, email in entries if email]


def parse_reply_to(apps, schema_editor):
    MailMessage = apps.get_model("mail", "MailMessage")
    db = schema_editor.connection.alias
    rows = (
        MailMessage.objects.using(db)
        .exclude(reply_to="")
        .only("uuid", "reply_to")
        .iterator(chunk_size=1000)
    )
    for batch in batched(rows, 1000, strict=False):
        for message in batch:
            message.reply_to_addresses = _parse(message.reply_to)
        MailMessage.objects.using(db).bulk_update(batch, ["reply_to_addresses"])


class Migration(migrations.Migration):
    dependencies = [
        ("mail", "0041_mailmessage_is_answered"),
    ]

    operations = [
        migrations.AddField(
            model_name="mailmessage",
            name="reply_to_addresses",
            field=models.JSONField(default=list),
        ),
        migrations.RunPython(parse_reply_to, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="mailmessage",
            name="reply_to",
        ),
    ]
