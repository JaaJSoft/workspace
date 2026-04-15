from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.notifications.models import Notification
from workspace.notifications.services.notifications import get_unread_count, notify, notify_many

User = get_user_model()


class NotifyTests(TestCase):

    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username='alice', password='pass')

    def tearDown(self):
        cache.clear()

    @patch('workspace.notifications.services.notifications.send_push_notification')
    @patch('workspace.notifications.services.notifications.notify_sse')
    def test_creates_notification(self, mock_sse, mock_push):
        notif = notify(
            recipient=self.alice, origin='chat', title='New message',
            body='Hello', url='/chat/123',
        )
        self.assertEqual(notif.recipient, self.alice)
        self.assertEqual(notif.title, 'New message')
        self.assertEqual(Notification.objects.count(), 1)

    @patch('workspace.notifications.services.notifications.send_push_notification')
    @patch('workspace.notifications.services.notifications.notify_sse')
    def test_triggers_sse(self, mock_sse, mock_push):
        notify(recipient=self.alice, origin='chat', title='Test')
        mock_sse.assert_called_with('notifications', self.alice.id)

    @patch('workspace.notifications.services.notifications.send_push_notification')
    @patch('workspace.notifications.services.notifications.notify_sse')
    def test_triggers_push_for_normal_priority(self, mock_sse, mock_push):
        notify(recipient=self.alice, origin='chat', title='Test')
        mock_push.delay.assert_called_once()

    @patch('workspace.notifications.services.notifications.send_push_notification')
    @patch('workspace.notifications.services.notifications.notify_sse')
    def test_skips_push_for_low_priority(self, mock_sse, mock_push):
        notify(recipient=self.alice, origin='chat', title='Test', priority='low')
        mock_push.delay.assert_not_called()

    @patch('workspace.notifications.services.notifications.send_push_notification')
    @patch('workspace.notifications.services.notifications.notify_sse')
    def test_invalidates_unread_cache(self, mock_sse, mock_push):
        cache.set(f'notif:unread:{self.alice.pk}', 0, 300)
        notify(recipient=self.alice, origin='chat', title='Test')
        self.assertIsNone(cache.get(f'notif:unread:{self.alice.pk}'))


class NotifyManyTests(TestCase):

    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.bob = User.objects.create_user(username='bob', password='pass')

    def tearDown(self):
        cache.clear()

    @patch('workspace.notifications.services.notifications.send_push_notification')
    @patch('workspace.notifications.services.notifications.notify_sse')
    def test_creates_notifications_for_all_recipients(self, mock_sse, mock_push):
        notifs = notify_many(
            recipients=[self.alice, self.bob], origin='files',
            title='Shared', body='File shared',
        )
        self.assertEqual(len(notifs), 2)
        self.assertEqual(Notification.objects.count(), 2)

    @patch('workspace.notifications.services.notifications.send_push_notification')
    @patch('workspace.notifications.services.notifications.notify_sse')
    def test_triggers_sse_for_each_recipient(self, mock_sse, mock_push):
        notify_many(
            recipients=[self.alice, self.bob], origin='files', title='Shared',
        )
        self.assertEqual(mock_sse.call_count, 2)

    @patch('workspace.notifications.services.notifications.send_push_notification')
    @patch('workspace.notifications.services.notifications.notify_sse')
    def test_skips_push_for_low_priority(self, mock_sse, mock_push):
        notify_many(
            recipients=[self.alice, self.bob], origin='files',
            title='Shared', priority='low',
        )
        mock_push.delay.assert_not_called()


class GetUnreadCountTests(TestCase):

    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username='alice', password='pass')

    def tearDown(self):
        cache.clear()

    def test_returns_zero_when_no_notifications(self):
        self.assertEqual(get_unread_count(self.alice), 0)

    def test_counts_unread_notifications(self):
        Notification.objects.create(
            recipient=self.alice, origin='chat', icon='msg', title='Test1',
        )
        Notification.objects.create(
            recipient=self.alice, origin='chat', icon='msg', title='Test2',
        )
        self.assertEqual(get_unread_count(self.alice), 2)

    def test_excludes_read_notifications(self):
        from django.utils import timezone
        Notification.objects.create(
            recipient=self.alice, origin='chat', icon='msg', title='Read',
            read_at=timezone.now(),
        )
        self.assertEqual(get_unread_count(self.alice), 0)

    def test_caches_result(self):
        self.assertEqual(get_unread_count(self.alice), 0)
        # Add a notification — count should still be cached as 0
        Notification.objects.create(
            recipient=self.alice, origin='chat', icon='msg', title='New',
        )
        self.assertEqual(get_unread_count(self.alice), 0)  # cached
