from django.db import migrations, models


def copy_group_to_groups(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    through = Project.groups.through
    through.objects.bulk_create(
        through(project_id=project.uuid, group_id=project.group_id)
        for project in Project.objects.exclude(group=None).only("uuid", "group_id")
    )


def copy_groups_to_group(apps, schema_editor):
    # Lossy by necessity: the old schema has a single FK slot, so only the
    # lowest-id group of each project survives a reverse migration; any
    # additional attached groups are dropped.
    Project = apps.get_model("projects", "Project")
    through = Project.groups.through
    for row in through.objects.order_by("project_id", "group_id"):
        Project.objects.filter(uuid=row.project_id, group=None).update(
            group_id=row.group_id
        )


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("projects", "0007_alter_taskevent_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="groups",
            field=models.ManyToManyField(
                blank=True, related_name="projects", to="auth.group"
            ),
        ),
        migrations.RunPython(copy_group_to_groups, copy_groups_to_group),
        migrations.RemoveField(
            model_name="project",
            name="group",
        ),
    ]
