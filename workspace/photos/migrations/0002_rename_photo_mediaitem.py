import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("files", "0056_fileshare_project_target"),
        ("photos", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel(old_name="Photo", new_name="MediaItem"),
        migrations.AlterField(
            model_name="mediaitem",
            name="file",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="media_item",
                to="files.file",
            ),
        ),
        migrations.RenameIndex(
            model_name="mediaitem",
            new_name="media_item_taken_at_idx",
            old_name="photo_taken_at_idx",
        ),
    ]
