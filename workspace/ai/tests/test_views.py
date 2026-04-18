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
        self.bot = BotProfile.objects.create(
            user=self.bot_user,
            system_prompt='You are helpful.',
            description='General assistant',
            is_public=True,
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

    def test_private_bot_hidden_from_list(self):
        self.bot.is_public = False
        self.bot.save()
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/v1/ai/bots')
        usernames = [b['username'] for b in resp.data]
        self.assertNotIn('test-assistant', usernames)

    def test_private_bot_visible_to_allowed_user(self):
        self.bot.is_public = False
        self.bot.save()
        self.bot.allowed_users.add(self.user)
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/v1/ai/bots')
        usernames = [b['username'] for b in resp.data]
        self.assertIn('test-assistant', usernames)

    def test_inactive_bot_user_hidden(self):
        self.bot_user.is_active = False
        self.bot_user.save()
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/v1/ai/bots')
        usernames = [b['username'] for b in resp.data]
        self.assertNotIn('test-assistant', usernames)

    def test_inactive_bot_user_hidden_even_for_superuser(self):
        admin = User.objects.create_superuser(username='admin', password='pass123')
        self.bot_user.is_active = False
        self.bot_user.save()
        self.client.force_authenticate(admin)
        resp = self.client.get('/api/v1/ai/bots')
        usernames = [b['username'] for b in resp.data]
        self.assertNotIn('test-assistant', usernames)

    def test_inactive_bot_user_hidden_even_if_allowed(self):
        self.bot.is_public = False
        self.bot.save()
        self.bot.allowed_users.add(self.user)
        self.bot_user.is_active = False
        self.bot_user.save()
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/v1/ai/bots')
        usernames = [b['username'] for b in resp.data]
        self.assertNotIn('test-assistant', usernames)

    def test_private_bot_visible_to_creator(self):
        self.bot.is_public = False
        self.bot.created_by = self.user
        self.bot.save()
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/v1/ai/bots')
        usernames = [b['username'] for b in resp.data]
        self.assertIn('test-assistant', usernames)

    def test_private_bot_visible_to_allowed_group(self):
        from django.contrib.auth.models import Group
        group = Group.objects.create(name='testers')
        group.user_set.add(self.user)
        self.bot.is_public = False
        self.bot.save()
        self.bot.allowed_groups.add(group)
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/v1/ai/bots')
        usernames = [b['username'] for b in resp.data]
        self.assertIn('test-assistant', usernames)

    def test_superuser_sees_all_active_bots(self):
        admin = User.objects.create_superuser(username='admin', password='pass123')
        self.bot.is_public = False
        self.bot.save()
        self.client.force_authenticate(admin)
        resp = self.client.get('/api/v1/ai/bots')
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


from unittest.mock import patch

from workspace.mail.models import MailAccount, MailFolder, MailMessage


@override_settings(AI_API_KEY='test-key')
class ClassifyViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='clsviewuser', password='pass123')
        self.account = MailAccount.objects.create(
            owner=self.user, email='cls@test.com',
            imap_host='imap.test.com', smtp_host='smtp.test.com',
            username='cls@test.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.msg = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=1,
            subject='Test',
        )

    def test_unauthenticated_rejected(self):
        resp = self.client.post('/api/v1/ai/tasks/mail/classify', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(AI_API_KEY='')
    def test_ai_not_configured(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post('/api/v1/ai/tasks/mail/classify', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch('workspace.ai.tasks.classify_mail_messages.delay')
    def test_creates_task_for_unclassified(self, mock_delay):
        self.client.force_authenticate(self.user)
        resp = self.client.post('/api/v1/ai/tasks/mail/classify', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn('uuid', resp.data)
        mock_delay.assert_called_once()

    def test_validates_account_ownership(self):
        other_user = User.objects.create_user(username='other', password='pass123')
        self.client.force_authenticate(other_user)
        resp = self.client.post('/api/v1/ai/tasks/mail/classify', {
            'account_id': str(self.account.uuid),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch('workspace.ai.tasks.classify_mail_messages.delay')
    def test_rate_limit(self, mock_delay):
        self.client.force_authenticate(self.user)
        resp1 = self.client.post('/api/v1/ai/tasks/mail/classify', {}, format='json')
        self.assertEqual(resp1.status_code, status.HTTP_202_ACCEPTED)
        resp2 = self.client.post('/api/v1/ai/tasks/mail/classify', {}, format='json')
        self.assertEqual(resp2.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch('workspace.ai.tasks.classify_mail_messages.delay')
    def test_no_unclassified_messages(self, mock_delay):
        from workspace.mail.models import MailMessageLabel
        label = self.account.labels.first()
        MailMessageLabel.objects.create(message=self.msg, label=label)
        self.client.force_authenticate(self.user)
        resp = self.client.post('/api/v1/ai/tasks/mail/classify', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch('workspace.ai.tasks.classify_mail_messages.delay')
    def test_excludes_already_labeled_messages(self, mock_delay):
        from workspace.mail.models import MailMessageLabel
        label = self.account.labels.first()
        MailMessageLabel.objects.create(message=self.msg, label=label)

        self.client.force_authenticate(self.user)
        resp = self.client.post('/api/v1/ai/tasks/mail/classify')
        self.assertEqual(resp.status_code, 202)
        from workspace.ai.models import AITask
        ai_task = AITask.objects.get(pk=resp.data['uuid'])
        self.assertEqual(ai_task.input_data['message_uuids'], [])
