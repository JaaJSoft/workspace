from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('files', '0012_fileshare_permission'),
    ]

    operations = [
        migrations.AddField(
            model_name='file',
            name='has_thumbnail',
            field=models.BooleanField(default=False),
        ),
    ]
