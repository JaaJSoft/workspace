import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0051_filescan_content_hash"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="filescan",
            name="overridden_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="filescan",
            name="overridden_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="filescan",
            name="override_reason",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AlterModelOptions(
            name="filescan",
            options={
                "permissions": [("override_filescan", "Can clear a malware quarantine")]
            },
        ),
    ]
