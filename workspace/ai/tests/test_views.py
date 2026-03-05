import uuid

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.ai.models import AITask, BotProfile, UserMemory

User = get_user_model()


class BotListTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')
        self.bot_user = User.objects.create_user(
            username='test-assistant', first_name='AI', last_name='Assistant',
        )
        BotProfile.objects.create(
            user=self.bot_user,
            system_prompt='You are helpful.',
            description='General assistant',
        )

    def test_unauthenticated_rejected(self):
        resp = self.client.get('/api/v1/ai/bots')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_bots(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/v1/ai/bots')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        usernames = [b['username'] for b in resp.data]
        self.assertIn('test-assistant', usernames)


@override_settings(AI_API_KEY='test-key')
class SummarizeViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')

    def test_unauthenticated_rejected(self):
        resp = self.client.post('/api/v1/ai/tasks/mail/summarize', {})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(AI_API_KEY='')
    def test_ai_not_configured(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post('/api/v1/ai/tasks/mail/summarize', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_message_not_found(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post('/api/v1/ai/tasks/mail/summarize', {
            'message_id': str(uuid.uuid4()),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(AI_API_KEY='test-key')
class TaskDetailViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')
        self.task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.SUMMARIZE,
            status=AITask.Status.COMPLETED,
            result='Summary of the email.',
        )

    def test_get_own_task(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f'/api/v1/ai/tasks/{self.task.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['result'], 'Summary of the email.')

    def test_cannot_see_other_users_task(self):
        other = User.objects.create_user(username='other', password='pass123')
        self.client.force_authenticate(other)
        resp = self.client.get(f'/api/v1/ai/tasks/{self.task.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class MemoryAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='user', password='pass123')
        self.bot_user = User.objects.create_user(username='bot', password='pass123')
        BotProfile.objects.create(user=self.bot_user)
        self.client.force_authenticate(self.user)

    def test_list_memories(self):
        UserMemory.objects.create(user=self.user, bot=self.bot_user, key='name', content='Pierre')
        UserMemory.objects.create(user=self.user, bot=self.bot_user, key='lang', content='Python')
        resp = self.client.get('/api/v1/ai/memories')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 2)

    def test_list_memories_only_own(self):
        other = User.objects.create_user(username='other', password='pass123')
        UserMemory.objects.create(user=self.user, bot=self.bot_user, key='name', content='Pierre')
        UserMemory.objects.create(user=other, bot=self.bot_user, key='name', content='Jean')
        resp = self.client.get('/api/v1/ai/memories')
        self.assertEqual(len(resp.json()), 1)

    def test_delete_memory(self):
        mem = UserMemory.objects.create(user=self.user, bot=self.bot_user, key='name', content='Pierre')
        resp = self.client.delete(f'/api/v1/ai/memories/{mem.id}')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(UserMemory.objects.filter(pk=mem.id).exists())

    def test_delete_other_user_memory_404(self):
        other = User.objects.create_user(username='other', password='pass123')
        mem = UserMemory.objects.create(user=other, bot=self.bot_user, key='name', content='Jean')
        resp = self.client.delete(f'/api/v1/ai/memories/{mem.id}')
        self.assertEqual(resp.status_code, 404)

    def test_patch_memory(self):
        mem = UserMemory.objects.create(user=self.user, bot=self.bot_user, key='name', content='Pierre')
        resp = self.client.patch(
            f'/api/v1/ai/memories/{mem.id}',
            data={'content': 'Paul'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        mem.refresh_from_db()
        self.assertEqual(mem.content, 'Paul')
