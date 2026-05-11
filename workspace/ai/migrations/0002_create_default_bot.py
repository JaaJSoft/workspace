from django.db import migrations


def create_default_bot(apps, schema_editor):
    """Create the default AI Assistant bot if AI is configured."""
    from django.conf import settings

    if not settings.AI_API_KEY:
        return

    db = schema_editor.connection.alias
    User = apps.get_model('auth', 'User')
    BotProfile = apps.get_model('ai', 'BotProfile')

    if BotProfile.objects.using(db).exists():
        return

    bot_user, created = User.objects.using(db).get_or_create(
        username='assistant',
        defaults={
            'first_name': 'AI',
            'last_name': 'Assistant',
            'is_active': True,
        },
    )
    if created:
        from django.contrib.auth.hashers import make_password
        bot_user.password = make_password(None)
        bot_user.save(update_fields=['password'], using=db)

    BotProfile.objects.using(db).get_or_create(
        user=bot_user,
        defaults={
            'system_prompt': (
                'You are a helpful assistant integrated into a workspace application. '
                'You respond concisely and helpfully. You can answer questions, help with writing, '
                'and assist with various tasks. Respond in the same language as the user\'s message.'
            ),
            'description': 'General-purpose AI assistant',
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('ai', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_default_bot, migrations.RunPython.noop),
    ]
