from django.db import migrations, models
from django.db.models import F


def backfill_closed_at(apps, schema_editor):
    """Seed closed_at from updated_at for goals closed before this migration.

    Approximate for a goal written to after closing, but the alternative is a
    NULL that hides every already-finished goal from the recall window.
    """
    AgentGoal = apps.get_model("ai", "AgentGoal")
    db = schema_editor.connection.alias
    AgentGoal.objects.using(db).filter(
        status__in=["completed", "abandoned"], closed_at__isnull=True
    ).update(closed_at=F("updated_at"))


class Migration(migrations.Migration):
    dependencies = [
        ("ai", "0018_agentgoal_mission_brief"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentgoal",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_closed_at, migrations.RunPython.noop),
    ]
