"""Take every bot out of the group conversations.

A bot runs its tools with the identity of whoever addressed it, and the raw
tool results and the history it replays are shown to every member, so in a
group one member's mail, files or calendar reached all the others. Bots now
only talk in a direct conversation with one person.

Ends each bot membership of a group the way leaving does (``left_at``), which
keeps the messages it already posted, and stops the agent goals and scheduled
messages bound to a group: they would only be refused at their next run.

Historical models only, and the logic inlined, so the migration keeps meaning
what it meant when it ran.
"""

from django.db import migrations
from django.utils import timezone

GROUP = "group"
GOAL_OPEN_STATUSES = ("active", "paused")
GOAL_ABANDONED = "abandoned"
GOAL_OUTCOME = "Stopped: bots only answer in a direct conversation."


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    ConversationMember = apps.get_model("chat", "ConversationMember")
    AgentGoal = apps.get_model("ai", "AgentGoal")
    ScheduledMessage = apps.get_model("ai", "ScheduledMessage")
    now = timezone.now()

    ConversationMember.objects.using(db).filter(
        conversation__kind=GROUP,
        left_at__isnull=True,
        user__bot_profile__isnull=False,
    ).update(left_at=now)

    AgentGoal.objects.using(db).filter(
        conversation__kind=GROUP,
        status__in=GOAL_OPEN_STATUSES,
    ).update(
        status=GOAL_ABANDONED,
        outcome=GOAL_OUTCOME,
        closed_at=now,
        updated_at=now,
    )

    ScheduledMessage.objects.using(db).filter(
        conversation__kind=GROUP,
        is_active=True,
    ).update(is_active=False, updated_at=now)


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0030_name_untitled_groups"),
        ("ai", "0025_aitask_answer_figures"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
