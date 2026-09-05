from django.db import migrations

# Frozen copy of services.projects.DEFAULT_STATUSES at the time new projects
# started being seeded with colors - a later change to the live constant must
# not rewrite what this migration did.
DEFAULT_STATUS_COLORS = [
    ("Backlog", "backlog", "#a855f7"),
    ("To do", "active", "#3b82f6"),
    ("In progress", "active", "#eab308"),
    ("Done", "done", "#22c55e"),
]


def colorize(apps, schema_editor):
    """Give the seeded default columns their palette color.

    Only rows still matching a default (name, category) pair AND still
    uncolored are touched: a renamed column or one the user already
    colored keeps its state.
    """
    TaskStatus = apps.get_model("projects", "TaskStatus")
    db = schema_editor.connection.alias

    for name, category, color in DEFAULT_STATUS_COLORS:
        TaskStatus.objects.using(db).filter(name=name, category=category, color="").update(
            color=color
        )


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0020_alter_taskevent_type_tasklink"),
    ]

    operations = [
        migrations.RunPython(colorize, migrations.RunPython.noop),
    ]
