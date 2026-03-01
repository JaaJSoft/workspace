from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.ai.models import AITask, BotProfile

User = get_user_model()


class BotProfileTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='pass123')
        self.bot_user = User.objects.create_user(
            username='test-bot', first_name='Test', last_name='Bot',
        )
        self.bot = BotProfile.objects.create(
            user=self.bot_user,
            system_prompt='You are a test bot.',
            model='gpt-4o',
            description='A test bot',
            created_by=self.admin,
        )

    def test_str(self):
        self.assertEqual(str(self.bot), 'Bot: Test Bot')

    def test_get_model_with_override(self):
        self.assertEqual(self.bot.get_model(), 'gpt-4o')

    @override_settings(AI_MODEL='gpt-4o-mini')
    def test_get_model_falls_back_to_setting(self):
        self.bot.model = ''
        self.bot.save()
        self.assertEqual(self.bot.get_model(), 'gpt-4o-mini')


class AITaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')

    def test_create_task(self):
        task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.SUMMARIZE,
            input_data={'message_id': 'test-uuid'},
        )
        self.assertEqual(task.status, AITask.Status.PENDING)
        self.assertEqual(task.task_type, 'summarize')
