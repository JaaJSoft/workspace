from django.db import migrations


def create_system_user(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    User.objects.get_or_create(
        username='system',
        defaults={
            'is_active': False,
            'email': 'system@localhost',
        },
    )


def remove_system_user(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    User.objects.filter(username='system').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('chat', '0012_add_call_models'),
    ]

    operations = [
        migrations.RunPython(create_system_user, remove_system_user),
    ]
