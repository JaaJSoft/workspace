from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0053_folder_share_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="tag",
            name="is_favorite",
            field=models.BooleanField(default=False),
        ),
    ]
