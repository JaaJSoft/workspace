from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ai", "0023_botprofile_can_send_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="aitask",
            name="generation_seconds",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
