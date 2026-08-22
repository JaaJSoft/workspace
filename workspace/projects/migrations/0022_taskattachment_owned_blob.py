from django.core.files.base import File as DjangoFile
from django.db import migrations, models

import workspace.projects.models


def copy_linked_files(apps, schema_editor):
    """Turn each link row into a task-owned copy of the workspace file.

    Rows whose source blob is gone are dropped: without content the
    attachment cannot be served, and the old model would have hidden it
    behind the vanished file anyway.
    """
    TaskAttachment = apps.get_model("projects", "TaskAttachment")
    db = schema_editor.connection.alias
    for att in TaskAttachment.objects.using(db).select_related("source_file").iterator():
        src = att.source_file
        try:
            with src.content.open("rb") as fh:
                att.original_name = (src.name or "file")[:255]
                att.mime_type = src.mime_type or "application/octet-stream"
                att.type = src.type or "unknown"
                att.category = src.category or "unknown"
                att.viewer = src.viewer or ""
                att.size = src.size or 0
                att.file.save(src.name or "file", DjangoFile(fh), save=True)
        except FileNotFoundError, OSError:
            att.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0021_default_status_colors"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="taskattachment",
            name="unique_task_attachment",
        ),
        migrations.RenameField(
            model_name="taskattachment",
            old_name="file",
            new_name="source_file",
        ),
        migrations.AddField(
            model_name="taskattachment",
            name="file",
            field=models.FileField(
                default="",
                max_length=500,
                upload_to=workspace.projects.models.task_attachment_upload_path,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="taskattachment",
            name="original_name",
            field=models.CharField(default="", max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="taskattachment",
            name="mime_type",
            field=models.CharField(default="application/octet-stream", max_length=255),
        ),
        migrations.AddField(
            model_name="taskattachment",
            name="type",
            field=models.CharField(default="unknown", max_length=50),
        ),
        migrations.AddField(
            model_name="taskattachment",
            name="category",
            field=models.CharField(default="unknown", max_length=20),
        ),
        migrations.AddField(
            model_name="taskattachment",
            name="viewer",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="taskattachment",
            name="size",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.RunPython(copy_linked_files, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="taskattachment",
            name="source_file",
        ),
    ]
