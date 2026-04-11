"""Tests for workspace.notifications.tasks.send_push_notification.

pywebpush.webpush is patched out — the suite never makes real HTTP calls.
"""

from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from pywebpush import WebPushException

from workspace.notifications import tasks as notif_tasks
from workspace.notifications.models import Notification, PushSubscription

User = get_user_model()

VALID_SETTINGS = dict(
    WEBPUSH_VAPID_PRIVATE_KEY='fake-key',
    WEBPUSH_VAPID_CLAIMS={'sub': 'mailto:admin@example.com'},
)


def _make_notification(recipient):
    return Notification.objects.create(
        recipient=recipient,
        origin='chat',
        icon='bell',
        title='You got mail',
        body='Click to open',
        url='/chat/1',
    )


def _make_subscription(user, *, endpoint=None):
    return PushSubscription.objects.create(
        user=user,
        endpoint=endpoint or f'https://push.example.com/{uuid4()}',
        p256dh='p256dh-key',
        auth='auth-secret',
    )


class SendPushNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='recipient', password='pass')

    # ------------------------------------------------------------------
    # Early exits
    # ------------------------------------------------------------------

    @override_settings(WEBPUSH_VAPID_PRIVATE_KEY='')
    def test_skips_when_private_key_missing(self):
        notif = _make_notification(self.user)
        _make_subscription(self.user)
        with mock.patch('workspace.notifications.tasks.webpush') as webpush_mock, \
                mock.patch('workspace.notifications.tasks.is_active', return_value=False):
            notif_tasks.send_push_notification.run(str(notif.uuid))
        webpush_mock.assert_not_called()

    @override_settings(**VALID_SETTINGS)
    def test_skips_unknown_notification(self):
        with mock.patch('workspace.notifications.tasks.webpush') as webpush_mock:
            notif_tasks.send_push_notification.run(str(uuid4()))
        webpush_mock.assert_not_called()

    @override_settings(**VALID_SETTINGS)
    def test_skips_when_recipient_is_active(self):
        notif = _make_notification(self.user)
        _make_subscription(self.user)
        with mock.patch('workspace.notifications.tasks.webpush') as webpush_mock, \
                mock.patch('workspace.notifications.tasks.is_active', return_value=True):
            notif_tasks.send_push_notification.run(str(notif.uuid))
        webpush_mock.assert_not_called()

    @override_settings(**VALID_SETTINGS)
    def test_noop_when_user_has_no_subscription(self):
        notif = _make_notification(self.user)
        with mock.patch('workspace.notifications.tasks.webpush') as webpush_mock, \
                mock.patch('workspace.notifications.tasks.is_active', return_value=False):
            notif_tasks.send_push_notification.run(str(notif.uuid))
        webpush_mock.assert_not_called()

    @override_settings(
        WEBPUSH_VAPID_PRIVATE_KEY='fake-key',
        WEBPUSH_VAPID_CLAIMS={'sub': ''},
    )
    def test_skips_when_vapid_sub_claim_is_empty(self):
        notif = _make_notification(self.user)
        _make_subscription(self.user)
        with mock.patch('workspace.notifications.tasks.webpush') as webpush_mock, \
                mock.patch('workspace.notifications.tasks.is_active', return_value=False):
            notif_tasks.send_push_notification.run(str(notif.uuid))
        webpush_mock.assert_not_called()

    # ------------------------------------------------------------------
    # Happy path & error handling
    # ------------------------------------------------------------------

    @override_settings(**VALID_SETTINGS)
    def test_sends_push_to_every_subscription(self):
        notif = _make_notification(self.user)
        sub1 = _make_subscription(self.user, endpoint='https://push.example.com/a')
        sub2 = _make_subscription(self.user, endpoint='https://push.example.com/b')

        with mock.patch('workspace.notifications.tasks.webpush') as webpush_mock, \
                mock.patch('workspace.notifications.tasks.is_active', return_value=False):
            notif_tasks.send_push_notification.run(str(notif.uuid))

        self.assertEqual(webpush_mock.call_count, 2)
        endpoints = [
            call.kwargs['subscription_info']['endpoint']
            for call in webpush_mock.call_args_list
        ]
        self.assertEqual(set(endpoints), {sub1.endpoint, sub2.endpoint})

        # Payload fields are all surfaced.
        import orjson
        first_payload = orjson.loads(webpush_mock.call_args_list[0].kwargs['data'])
        self.assertEqual(first_payload['title'], 'You got mail')
        self.assertEqual(first_payload['body'], 'Click to open')
        self.assertEqual(first_payload['url'], '/chat/1')
        self.assertEqual(first_payload['origin'], 'chat')
        self.assertEqual(first_payload['icon'], 'bell')

        # VAPID credentials propagated.
        kwargs = webpush_mock.call_args_list[0].kwargs
        self.assertEqual(kwargs['vapid_private_key'], 'fake-key')
        self.assertEqual(kwargs['vapid_claims'], {'sub': 'mailto:admin@example.com'})

    @override_settings(**VALID_SETTINGS)
    def test_expired_subscription_is_deleted(self):
        notif = _make_notification(self.user)
        sub = _make_subscription(self.user)

        response = mock.Mock(status_code=410)
        exc = WebPushException('Gone', response=response)

        with mock.patch(
            'workspace.notifications.tasks.webpush', side_effect=exc,
        ), mock.patch('workspace.notifications.tasks.is_active', return_value=False):
            notif_tasks.send_push_notification.run(str(notif.uuid))

        self.assertFalse(PushSubscription.objects.filter(pk=sub.pk).exists())

    @override_settings(**VALID_SETTINGS)
    def test_not_found_subscription_is_deleted(self):
        notif = _make_notification(self.user)
        sub = _make_subscription(self.user)

        response = mock.Mock(status_code=404)
        exc = WebPushException('Not Found', response=response)

        with mock.patch(
            'workspace.notifications.tasks.webpush', side_effect=exc,
        ), mock.patch('workspace.notifications.tasks.is_active', return_value=False):
            notif_tasks.send_push_notification.run(str(notif.uuid))

        self.assertFalse(PushSubscription.objects.filter(pk=sub.pk).exists())

    @override_settings(**VALID_SETTINGS)
    def test_transient_webpush_error_keeps_subscription(self):
        notif = _make_notification(self.user)
        sub = _make_subscription(self.user)

        response = mock.Mock(status_code=500)
        exc = WebPushException('Server error', response=response)

        with mock.patch(
            'workspace.notifications.tasks.webpush', side_effect=exc,
        ), mock.patch('workspace.notifications.tasks.is_active', return_value=False):
            notif_tasks.send_push_notification.run(str(notif.uuid))

        self.assertTrue(PushSubscription.objects.filter(pk=sub.pk).exists())

    @override_settings(**VALID_SETTINGS)
    def test_unexpected_exception_is_swallowed(self):
        notif = _make_notification(self.user)
        sub = _make_subscription(self.user)

        with mock.patch(
            'workspace.notifications.tasks.webpush',
            side_effect=RuntimeError('boom'),
        ), mock.patch('workspace.notifications.tasks.is_active', return_value=False):
            # Must not raise.
            notif_tasks.send_push_notification.run(str(notif.uuid))

        # Subscription is untouched on unknown errors.
        self.assertTrue(PushSubscription.objects.filter(pk=sub.pk).exists())
