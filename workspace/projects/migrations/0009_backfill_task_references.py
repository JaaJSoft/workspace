from django.db import migrations
from django.db.models import OuterRef, Subquery

# unique_project_key is a pure string/set helper: safe to import from a
# migration. Note references.py itself imports models at module level,
# which is safe here because nothing queries at import time.
from workspace.projects.services.references import unique_project_key


def backfill(apps, schema_editor):
    """Key every project and number every task, oldest first.

    Deterministic iteration keeps key suffixes stable. Recomputes from
    scratch on purpose: that makes the function idempotent and testable
    against the final schema.
    """
    Project = apps.get_model("projects", "Project")
    Task = apps.get_model("projects", "Task")
    TaskEvent = apps.get_model("projects", "TaskEvent")

    taken = set()
    for project in Project.objects.order_by("created_at", "uuid").iterator():
        key = unique_project_key(project.name, taken=taken)
        taken.add(key)
        tasks = list(project.tasks.order_by("created_at", "uuid").only("uuid"))
        for i, task in enumerate(tasks, start=1):
            task.number = i
        Task.objects.bulk_update(tasks, ["number"], batch_size=500)
        project.key = key
        project.next_task_number = len(tasks) + 1
        project.save(update_fields=["key", "next_task_number"])

    TaskEvent.objects.filter(task__isnull=False).update(
        task_number=Subquery(
            Task.objects.filter(uuid=OuterRef("task_id")).values("number")[:1]
        )
    )


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0008_project_key_task_number"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
