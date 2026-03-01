from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from workspace.ai.models import BotProfile

User = get_user_model()


class Command(BaseCommand):
    help = 'Create an AI bot user with a BotProfile.'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='Bot username')
        parser.add_argument('--name', type=str, default='', help='Display name')
        parser.add_argument('--prompt', type=str, default='', help='System prompt')
        parser.add_argument('--model', type=str, default='', help='Model override')
        parser.add_argument('--description', type=str, default='', help='Bot description')

    def handle(self, *args, **options):
        username = options['username']
        name = options['name'] or username.replace('-', ' ').title()

        if User.objects.filter(username=username).exists():
            user = User.objects.get(username=username)
            self.stdout.write(f'User "{username}" already exists.')
        else:
            name_parts = name.split(' ', 1)
            user = User.objects.create_user(
                username=username,
                first_name=name_parts[0],
                last_name=name_parts[1] if len(name_parts) > 1 else '',
                is_active=True,
            )
            user.set_unusable_password()
            user.save()
            self.stdout.write(self.style.SUCCESS(f'Created user "{username}".'))

        profile, created = BotProfile.objects.update_or_create(
            user=user,
            defaults={
                'system_prompt': options['prompt'],
                'model': options['model'],
                'description': options['description'],
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f'Created BotProfile for "{username}".'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Updated BotProfile for "{username}".'))
