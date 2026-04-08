from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('calendar', '0009_externalcalendar'),
    ]

    operations = [
        migrations.RenameField(
            model_name='event',
            old_name='organizer_email',
            new_name='external_organizer',
        ),
    ]
