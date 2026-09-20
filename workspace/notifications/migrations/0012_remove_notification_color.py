from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0011_notification_stream"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="notification",
            name="color",
        ),
    ]
