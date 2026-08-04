import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.notifications.models import Notification, PushSubscription

User = get_user_model()

FAKE_VAPID_SETTINGS = {
    "WEBPUSH_VAPID_PRIVATE_KEY": "fake-private-key",
    "WEBPUSH_VAPID_PUBLIC_KEY": "fake-public-key",
    "WEBPUSH_VAPID_CLAIMS": {"sub": "mailto:test@example.com"},
}


@override_settings(**FAKE_VAPID_SETTINGS)
class SendPushNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="pushuser",
            email="push@test.com",
            password="pass123",
        )
        self.notif = Notification.objects.create(
            recipient=self.user,
            origin="test",
            icon="icon-test",
            title="Test Title",
            body="Test body",
            url="/test/url",
        )
        self.sub1 = PushSubscription.objects.create(
            user=self.user,
            endpoint="https://push.example.com/sub/1",
            p256dh="p256dh-key-1",
            auth="auth-key-1",
        )
        self.sub2 = PushSubscription.objects.create(
            user=self.user,
            endpoint="https://push.example.com/sub/2",
            p256dh="p256dh-key-2",
            auth="auth-key-2",
        )

    @patch("workspace.notifications.tasks.is_active", return_value=False)
    @patch("workspace.notifications.tasks.webpush")
    def test_sends_push_to_all_subscriptions(self, mock_webpush, _):
        from workspace.notifications.tasks import send_push_notification

        send_push_notification(str(self.notif.uuid))
        self.assertEqual(mock_webpush.call_count, 2)

    @patch("workspace.notifications.tasks.is_active", return_value=True)
    @patch("workspace.notifications.tasks.webpush")
    def test_skips_push_when_user_is_active(self, mock_webpush, mock_is_active):
        from workspace.notifications.tasks import send_push_notification

        # Patch apply_async so the deferred retry is not executed inline when
        # Celery runs in eager mode (dev settings).
        with patch.object(send_push_notification, "apply_async"):
            send_push_notification(str(self.notif.uuid))
        mock_is_active.assert_called_once_with(self.user.id)
        mock_webpush.assert_not_called()

    @patch("workspace.notifications.tasks.is_active", return_value=False)
    @patch("workspace.notifications.tasks.webpush")
    def test_sends_push_when_user_inactive(self, mock_webpush, _):
        from workspace.notifications.tasks import send_push_notification

        send_push_notification(str(self.notif.uuid))
        self.assertEqual(mock_webpush.call_count, 2)

    @patch("workspace.notifications.tasks.is_active", return_value=False)
    @patch("workspace.notifications.tasks.webpush")
    def test_deletes_subscription_on_410(self, mock_webpush, _):
        from pywebpush import WebPushException

        mock_response = MagicMock(status_code=410)
        mock_webpush.side_effect = WebPushException("Gone", response=mock_response)

        from workspace.notifications.tasks import send_push_notification

        send_push_notification(str(self.notif.uuid))

        self.assertEqual(PushSubscription.objects.filter(user=self.user).count(), 0)

    @patch("workspace.notifications.tasks.webpush")
    def test_noop_when_notification_not_found(self, mock_webpush):
        from workspace.notifications.tasks import send_push_notification

        send_push_notification(str(uuid.uuid4()))
        mock_webpush.assert_not_called()

    @override_settings(WEBPUSH_VAPID_PRIVATE_KEY="")
    @patch("workspace.notifications.tasks.webpush")
    def test_noop_when_vapid_not_configured(self, mock_webpush):
        from workspace.notifications.tasks import send_push_notification

        send_push_notification(str(self.notif.uuid))
        mock_webpush.assert_not_called()


@override_settings(**FAKE_VAPID_SETTINGS)
@patch("workspace.notifications.tasks.is_active", return_value=False)
@patch("workspace.notifications.tasks.webpush")
class PushSkipAndCooldownTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="cduser", password="pass")
        PushSubscription.objects.create(
            user=self.user,
            endpoint="https://push.example.com/cd/1",
            p256dh="p256dh-key",
            auth="auth-key",
        )
        from workspace.chat.models import Conversation

        self.conv = Conversation.objects.create(created_by=self.user)

    def tearDown(self):
        cache.clear()

    def _notif(self, **kwargs):
        defaults = {
            "recipient": self.user,
            "origin": "chat",
            "icon": "",
            "title": "t",
            "conversation": self.conv,
        }
        defaults.update(kwargs)
        return Notification.objects.create(**defaults)

    def test_skips_when_already_read(self, mock_webpush, _):
        from workspace.notifications.tasks import send_push_notification

        notif = self._notif(read_at=timezone.now())
        send_push_notification(str(notif.uuid))
        mock_webpush.assert_not_called()

    def test_second_push_same_source_within_cooldown_is_skipped(self, mock_webpush, _):
        from workspace.notifications.tasks import send_push_notification

        send_push_notification(str(self._notif().uuid))
        send_push_notification(str(self._notif().uuid))
        self.assertEqual(mock_webpush.call_count, 1)

    def test_urgent_bypasses_cooldown(self, mock_webpush, _):
        from workspace.notifications.tasks import send_push_notification

        send_push_notification(str(self._notif().uuid))
        send_push_notification(str(self._notif(priority="urgent").uuid))
        self.assertEqual(mock_webpush.call_count, 2)

    def test_sourceless_notifications_bypass_cooldown(self, mock_webpush, _):
        from workspace.notifications.tasks import send_push_notification

        send_push_notification(str(self._notif(conversation=None).uuid))
        send_push_notification(str(self._notif(conversation=None).uuid))
        self.assertEqual(mock_webpush.call_count, 2)

    def test_active_user_skip_does_not_consume_cooldown(self, mock_webpush, _):
        from workspace.notifications.tasks import send_push_notification

        with (
            patch("workspace.notifications.tasks.is_active", return_value=True),
            patch.object(send_push_notification, "apply_async"),
        ):
            send_push_notification(str(self._notif().uuid))
        mock_webpush.assert_not_called()
        send_push_notification(str(self._notif().uuid))
        self.assertEqual(mock_webpush.call_count, 1)

    def test_push_resumes_after_cooldown_expires(self, mock_webpush, _):
        from workspace.notifications.tasks import send_push_notification

        send_push_notification(str(self._notif().uuid))
        cache.clear()
        send_push_notification(str(self._notif().uuid))
        self.assertEqual(mock_webpush.call_count, 2)

    def test_failed_delivery_releases_cooldown(self, mock_webpush, _):
        from pywebpush import WebPushException

        from workspace.notifications.tasks import send_push_notification

        mock_webpush.side_effect = WebPushException(
            "boom", response=MagicMock(status_code=500)
        )
        send_push_notification(str(self._notif().uuid))
        mock_webpush.side_effect = None
        mock_webpush.reset_mock()
        send_push_notification(str(self._notif().uuid))
        self.assertEqual(mock_webpush.call_count, 1)

    def test_partial_delivery_keeps_cooldown(self, mock_webpush, _):
        from pywebpush import WebPushException

        from workspace.notifications.tasks import send_push_notification

        PushSubscription.objects.create(
            user=self.user,
            endpoint="https://push.example.com/cd/2",
            p256dh="p256dh-key-2",
            auth="auth-key-2",
        )
        mock_webpush.side_effect = [
            WebPushException("boom", response=MagicMock(status_code=500)),
            None,
        ]
        send_push_notification(str(self._notif().uuid))
        mock_webpush.side_effect = None
        mock_webpush.reset_mock()
        send_push_notification(str(self._notif().uuid))
        mock_webpush.assert_not_called()


@override_settings(**FAKE_VAPID_SETTINGS)
class ActiveUserRetryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="retryuser", password="pass")
        PushSubscription.objects.create(
            user=self.user,
            endpoint="https://push.example.com/retry/1",
            p256dh="p256dh-key",
            auth="auth-key",
        )
        self.notif = Notification.objects.create(
            recipient=self.user, origin="chat", title="t"
        )

    def tearDown(self):
        cache.clear()

    @patch("workspace.notifications.tasks.is_active", return_value=True)
    @patch("workspace.notifications.tasks.webpush")
    def test_active_skip_schedules_one_delayed_retry(self, mock_webpush, _):
        from workspace.notifications import tasks

        with patch.object(tasks.send_push_notification, "apply_async") as mock_apply:
            tasks.send_push_notification(str(self.notif.uuid))
        mock_webpush.assert_not_called()
        mock_apply.assert_called_once()
        self.assertEqual(
            mock_apply.call_args.kwargs["countdown"],
            tasks.ACTIVE_RETRY_DELAY_SECONDS,
        )

    @patch("workspace.notifications.tasks.is_active", return_value=True)
    @patch("workspace.notifications.tasks.webpush")
    def test_retry_pushes_despite_active_user(self, mock_webpush, _):
        from workspace.notifications import tasks

        with patch.object(tasks.send_push_notification, "apply_async") as mock_apply:
            tasks.send_push_notification(str(self.notif.uuid), is_retry=True)
        self.assertEqual(mock_webpush.call_count, 1)
        mock_apply.assert_not_called()

    @patch("workspace.notifications.tasks.is_active", return_value=True)
    @patch("workspace.notifications.tasks.webpush")
    def test_retry_skips_when_read_meanwhile(self, mock_webpush, _):
        from workspace.notifications import tasks

        self.notif.read_at = timezone.now()
        self.notif.save(update_fields=["read_at"])
        with patch.object(tasks.send_push_notification, "apply_async") as mock_apply:
            tasks.send_push_notification(str(self.notif.uuid), is_retry=True)
        mock_webpush.assert_not_called()
        mock_apply.assert_not_called()
