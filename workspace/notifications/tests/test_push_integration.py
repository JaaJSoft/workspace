from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class NotifyPushIntegrationTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='intuser', email='int@test.com', password='pass123',
        )

    @patch('workspace.notifications.services.send_push_notification')
    @patch('workspace.notifications.services.notify_sse')
    def test_notify_dispatches_push_for_normal_priority(self, _mock_sse, mock_push):
        from workspace.notifications.services import notify
        notif = notify(
            recipient=self.user,
            origin='test',
            title='Normal notification',
            priority='normal',
        )
        mock_push.delay.assert_called_once_with(str(notif.uuid))

    @patch('workspace.notifications.services.send_push_notification')
    @patch('workspace.notifications.services.notify_sse')
    def test_notify_skips_push_for_low_priority(self, _mock_sse, mock_push):
        from workspace.notifications.services import notify
        notify(
            recipient=self.user,
            origin='test',
            title='Low notification',
            priority='low',
        )
        mock_push.delay.assert_not_called()

    @patch('workspace.notifications.services.send_push_notification')
    @patch('workspace.notifications.services.notify_sse')
    def test_notify_many_dispatches_push_per_recipient(self, _mock_sse, mock_push):
        user2 = User.objects.create_user(
            username='intuser2', email='int2@test.com', password='pass123',
        )
        from workspace.notifications.services import notify_many
        notify_many(
            recipients=[self.user, user2],
            origin='test',
            title='Batch notification',
            priority='normal',
        )
        self.assertEqual(mock_push.delay.call_count, 2)

    @patch('workspace.notifications.services.send_push_notification')
    @patch('workspace.notifications.services.notify_sse')
    def test_notify_many_skips_push_for_low(self, _mock_sse, mock_push):
        user2 = User.objects.create_user(
            username='intuser2', email='int2@test.com', password='pass123',
        )
        from workspace.notifications.services import notify_many
        notify_many(
            recipients=[self.user, user2],
            origin='test',
            title='Low batch',
            priority='low',
        )
        mock_push.delay.assert_not_called()
