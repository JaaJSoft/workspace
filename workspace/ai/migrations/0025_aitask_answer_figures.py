from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ai", "0024_aitask_generation_seconds"),
    ]

    operations = [
        migrations.AddField(
            model_name="aitask",
            name="answer_tokens",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="aitask",
            name="answer_seconds",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
