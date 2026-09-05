from django.db import migrations

# Both helpers are pure string/set functions: safe to import from a
# migration. Note references.py itself imports models at module level,
# which is safe here because nothing queries at import time.
from workspace.projects.services.references import (
    personal_key_base,
    unique_project_key,
)


def rekey(apps, schema_editor):
    """Re-derive personal project keys from their owner's username.

    They were all seeded from the literal name "Personal", so the app-wide
    unique constraint handed them PERS, PERS2, PERS3... in creation order.

    *taken* tracks every key currently held, including the personal ones
    still awaiting their turn: a project never steals a key another row
    holds right now, so the batch needs no temporary keys to avoid
    tripping the unique constraint mid-loop.
    """
    Project = apps.get_model("projects", "Project")
    db = schema_editor.connection.alias

    taken = set(Project.objects.using(db).values_list("key", flat=True))
    taken.discard(None)
    personal = (
        Project.objects.using(db).filter(type="personal")
        .select_related("created_by")
        .order_by("created_at", "uuid")
    )
    for project in personal.iterator():
        # created_by is nullable (SET_NULL): an owner-less personal project
        # keeps the bare prefix and lands on a numeric suffix.
        username = getattr(project.created_by, "username", "") or ""
        taken.discard(project.key)
        key = unique_project_key(personal_key_base(username), taken=taken)
        taken.add(key)
        if key != project.key:
            project.key = key
            project.save(update_fields=["key"])


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0013_project_done_retention_days"),
    ]

    operations = [
        migrations.RunPython(rekey, migrations.RunPython.noop),
    ]
