from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ai', '0009_scheduled_message'),
    ]

    operations = [
        migrations.AddField(
            model_name='aitask',
            name='raw_messages',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
