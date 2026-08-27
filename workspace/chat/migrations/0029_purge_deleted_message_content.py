from django.db import migrations

# A live-code import inside a migration: purge_deleted_message_backlog only
# touches the historical models passed in below, but moving or renaming it
# would break `migrate` from scratch. If services.deletion ever loses that
# function, freeze a copy of it in this file instead of updating the import.
from workspace.chat.services.deletion import purge_deleted_message_backlog


def forwards(apps, schema_editor):
    purge_deleted_message_backlog(
        messages=apps.get_model("chat", "Message"),
        attachments=apps.get_model("chat", "MessageAttachment"),
        reactions=apps.get_model("chat", "Reaction"),
        link_previews=apps.get_model("chat", "MessageLinkPreview"),
        interactions=apps.get_model("chat", "MessageInteraction"),
        pins=apps.get_model("chat", "PinnedMessage"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0028_backfill_thread_roots"),
    ]

    operations = [
        # Irreversible: the text and the blobs are gone. RunPython.noop keeps
        # `migrate chat 0028` working rather than refusing to unapply, since
        # rolling back the schema has nothing to restore either way.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
