from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.chat.models import Conversation
from workspace.notifications.models import Notification
from workspace.notifications.services.notifications import notify_stream

User = get_user_model()


@patch("workspace.notifications.services.notifications.send_push_notification")
@patch("workspace.notifications.services.notifications.notify_sse")
class NotifyStreamTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.conv = Conversation.objects.create(created_by=self.alice)

    def tearDown(self):
        cache.clear()

    def _send(self, **kwargs):
        defaults = {
            "recipient_ids": [self.alice.pk, self.bob.pk],
            "source": self.conv,
            "origin": "chat",
            "title": "first",
        }
        defaults.update(kwargs)
        return notify_stream(**defaults)

    def test_creates_one_row_per_recipient(self, mock_sse, mock_push):
        self._send()
        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(Notification.objects.filter(conversation=self.conv).count(), 2)

    def test_second_call_merges_instead_of_stacking(self, mock_sse, mock_push):
        self._send()
        self._send(title="second", body="newer")
        self.assertEqual(Notification.objects.count(), 2)
        notif = Notification.objects.get(recipient=self.alice)
        self.assertEqual(notif.title, "second")
        self.assertEqual(notif.body, "newer")

    def test_merge_only_targets_unread(self, mock_sse, mock_push):
        from django.utils import timezone

        self._send()
        Notification.objects.update(read_at=timezone.now())
        self._send(title="second")
        self.assertEqual(Notification.objects.count(), 4)

    def test_priority_upgrades_but_never_downgrades(self, mock_sse, mock_push):
        self._send(priority_map={self.alice.pk: "high"})
        self._send()  # normal for everyone
        self.assertEqual(
            Notification.objects.get(recipient=self.alice).priority, "high"
        )
        self._send(priority_map={self.bob.pk: "high"})
        self.assertEqual(Notification.objects.get(recipient=self.bob).priority, "high")

    def test_push_only_for_created_rows(self, mock_sse, mock_push):
        self._send()
        self.assertEqual(mock_push.delay.call_count, 2)
        mock_push.delay.reset_mock()
        self._send(title="second")
        mock_push.delay.assert_not_called()

    def test_mention_merge_pushes_updated_row(self, mock_sse, mock_push):
        self._send()
        mock_push.delay.reset_mock()
        self._send(title="second", priority_map={self.alice.pk: "high"})
        notif = Notification.objects.get(recipient=self.alice)
        mock_push.delay.assert_called_once_with(str(notif.uuid))

    def test_normal_merge_into_high_row_does_not_push(self, mock_sse, mock_push):
        self._send(priority_map={self.alice.pk: "high"})
        mock_push.delay.reset_mock()
        self._send(title="second")
        mock_push.delay.assert_not_called()

    def test_low_priority_creates_do_not_push(self, mock_sse, mock_push):
        self._send(default_priority="low")
        mock_push.delay.assert_not_called()

    def test_empty_recipients_is_noop(self, mock_sse, mock_push):
        result = notify_stream(
            recipient_ids=[], source=self.conv, origin="chat", title="x"
        )
        self.assertEqual(result, [])
        self.assertEqual(Notification.objects.count(), 0)

    def test_merge_refreshes_url(self, mock_sse, mock_push):
        self._send(url="/a")
        self._send(title="second", url="/b")
        notif = Notification.objects.get(recipient=self.alice)
        self.assertEqual(notif.url, "/b")

    def test_merge_bumps_created_at(self, mock_sse, mock_push):
        self._send()
        first = Notification.objects.get(recipient=self.alice).created_at
        self._send(title="second")
        second = Notification.objects.get(recipient=self.alice).created_at
        self.assertGreater(second, first)
