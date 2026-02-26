import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.notifications.models import Notification, PushSubscription

User = get_user_model()

FAKE_VAPID_SETTINGS = {
    'WEBPUSH_VAPID_PRIVATE_KEY': 'fake-private-key',
    'WEBPUSH_VAPID_PUBLIC_KEY': 'fake-public-key',
    'WEBPUSH_VAPID_CLAIMS': {'sub': 'mailto:test@example.com'},
}


@override_settings(**FAKE_VAPID_SETTINGS)
class SendPushNotificationTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='pushuser', email='push@test.com', password='pass123',
        )
        self.notif = Notification.objects.create(
            recipient=self.user,
            origin='test',
            icon='icon-test',
            title='Test Title',
            body='Test body',
            url='/test/url',
        )
        self.sub1 = PushSubscription.objects.create(
            user=self.user,
            endpoint='https://push.example.com/sub/1',
            p256dh='p256dh-key-1',
            auth='auth-key-1',
        )
        self.sub2 = PushSubscription.objects.create(
            user=self.user,
            endpoint='https://push.example.com/sub/2',
            p256dh='p256dh-key-2',
            auth='auth-key-2',
        )

    @patch('workspace.notifications.tasks.is_active', return_value=False)
    @patch('workspace.notifications.tasks.webpush')
    def test_sends_push_to_all_subscriptions(self, mock_webpush, _):
        from workspace.notifications.tasks import send_push_notification
        send_push_notification(str(self.notif.uuid))
        self.assertEqual(mock_webpush.call_count, 2)

    @patch('workspace.notifications.tasks.is_active', return_value=True)
    @patch('workspace.notifications.tasks.webpush')
    def test_skips_push_when_user_is_active(self, mock_webpush, mock_is_active):
        from workspace.notifications.tasks import send_push_notification
        send_push_notification(str(self.notif.uuid))
        mock_is_active.assert_called_once_with(self.user.id)
        mock_webpush.assert_not_called()

    @patch('workspace.notifications.tasks.is_active', return_value=False)
    @patch('workspace.notifications.tasks.webpush')
    def test_sends_push_when_user_inactive(self, mock_webpush, _):
        from workspace.notifications.tasks import send_push_notification
        send_push_notification(str(self.notif.uuid))
        self.assertEqual(mock_webpush.call_count, 2)

    @patch('workspace.notifications.tasks.is_active', return_value=False)
    @patch('workspace.notifications.tasks.webpush')
    def test_deletes_subscription_on_410(self, mock_webpush, _):
        from pywebpush import WebPushException
        mock_response = MagicMock(status_code=410)
        mock_webpush.side_effect = WebPushException("Gone", response=mock_response)

        from workspace.notifications.tasks import send_push_notification
        send_push_notification(str(self.notif.uuid))

        self.assertEqual(PushSubscription.objects.filter(user=self.user).count(), 0)

    @patch('workspace.notifications.tasks.webpush')
    def test_noop_when_notification_not_found(self, mock_webpush):
        from workspace.notifications.tasks import send_push_notification
        send_push_notification(str(uuid.uuid4()))
        mock_webpush.assert_not_called()

    @override_settings(WEBPUSH_VAPID_PRIVATE_KEY='')
    @patch('workspace.notifications.tasks.webpush')
    def test_noop_when_vapid_not_configured(self, mock_webpush):
        from workspace.notifications.tasks import send_push_notification
        send_push_notification(str(self.notif.uuid))
        mock_webpush.assert_not_called()
