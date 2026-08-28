"""Give every group conversation a name.

A group used to be displayed as the names of its first members whenever its
creator left the title blank. The sidebar now reads the stored title instead,
so a group row costs nothing in member rows - which only works if the rows that
predate the rule carry a title too.
"""

from django.db import migrations

# A live-code import inside a migration: backfill_group_titles only touches the
# historical models passed in below, but moving or renaming it would break
# `migrate` from scratch. If services.conversations ever loses that function,
# freeze a copy of it in this file instead of updating the import.
from workspace.chat.services.conversations import backfill_group_titles


def forwards(apps, schema_editor):
    backfill_group_titles(
        apps.get_model("chat", "Conversation"),
        apps.get_model("chat", "ConversationMember"),
    )


class Migration(migrations.Migration):
    dependencies = [("chat", "0029_purge_deleted_message_content")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
