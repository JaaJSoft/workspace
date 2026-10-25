"""Give every group conversation its admins.

The creator becomes the first admin of each group they are still in. A group
whose creator already left had nobody able to remove members, so its oldest
active member takes the role, the same rule as a last admin leaving.

Historical models only, and the logic inlined, so the migration keeps meaning
what it meant when it ran.
"""

from django.db import migrations, models
from django.db.models import Exists, F, OuterRef

GROUP = "group"
ADMIN = "admin"


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    Conversation = apps.get_model("chat", "Conversation")
    ConversationMember = apps.get_model("chat", "ConversationMember")

    ConversationMember.objects.using(db).filter(
        conversation__kind=GROUP,
        left_at__isnull=True,
        user_id=F("conversation__created_by_id"),
    ).update(role=ADMIN)

    active = ConversationMember.objects.using(db).filter(
        conversation=OuterRef("pk"), left_at__isnull=True
    )
    headless = (
        Conversation.objects.using(db)
        .filter(kind=GROUP)
        .filter(Exists(active))
        .exclude(Exists(active.filter(role=ADMIN)))
        .values_list("pk", flat=True)
    )
    for conversation_id in list(headless):
        oldest = (
            ConversationMember.objects.using(db)
            .filter(conversation_id=conversation_id, left_at__isnull=True)
            .order_by("joined_at", "uuid")
            .values_list("pk", flat=True)
            .first()
        )
        ConversationMember.objects.using(db).filter(pk=oldest).update(role=ADMIN)


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0031_bots_leave_group_conversations"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversationmember",
            name="role",
            field=models.CharField(
                choices=[("admin", "Admin"), ("member", "Member")],
                default="member",
                max_length=6,
            ),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
