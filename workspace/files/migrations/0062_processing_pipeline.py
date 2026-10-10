import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0061_file_last_event_at"),
    ]

    operations = [
        migrations.RenameModel(old_name="ThumbnailFailure", new_name="ProcessingFailure"),
        migrations.AlterField(
            model_name="processingfailure",
            name="file",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="processing_failures",
                to="files.file",
            ),
        ),
        # Every row recorded so far is a thumbnail failure.
        migrations.AddField(
            model_name="processingfailure",
            name="processor",
            field=models.CharField(default="thumbnails", max_length=32),
            preserve_default=False,
        ),
        migrations.AddConstraint(
            model_name="processingfailure",
            constraint=models.UniqueConstraint(
                fields=("file", "processor"),
                name="files_processingfailure_file_processor",
            ),
        ),
        migrations.AddIndex(
            model_name="processingfailure",
            index=models.Index(
                fields=["processor", "attempts"],
                name="files_procfail_proc_attempts",
            ),
        ),
        migrations.AddField(
            model_name="file",
            name="processing_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("ready", "Ready"),
                ],
                default="ready",
                max_length=16,
            ),
        ),
    ]
