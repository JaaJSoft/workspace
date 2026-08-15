from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import Conversation
from workspace.notifications.models import Notification
from workspace.notifications.services.notifications import (
    get_unread_count,
    settle_sources,
)

User = get_user_model()


class SettleSourcesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.conv = Conversation.objects.create(created_by=self.alice)
        self.other_conv = Conversation.objects.create(created_by=self.alice)

    def tearDown(self):
        cache.clear()

    def _notif(self, recipient, source=None, priority="normal", read=False):
        return Notification.objects.create(
            recipient=recipient,
            origin="chat",
            icon="i",
            title="t",
            priority=priority,
            conversation=source,
            read_at=timezone.now() if read else None,
        )

    def test_settles_every_recipient(self):
        self._notif(self.alice, source=self.conv)
        self._notif(self.bob, source=self.conv)

        marked = settle_sources([self.conv])

        self.assertEqual(marked, 2)
        self.assertFalse(Notification.objects.filter(read_at__isnull=True).exists())

    def test_leaves_other_sources_unread(self):
        self._notif(self.alice, source=self.conv)
        untouched = self._notif(self.alice, source=self.other_conv)

        settle_sources([self.conv])

        untouched.refresh_from_db()
        self.assertIsNone(untouched.read_at)

    def test_max_priority_spares_higher_rows(self):
        reminder = self._notif(self.alice, source=self.conv, priority="normal")
        mention = self._notif(self.bob, source=self.conv, priority="high")

        marked = settle_sources([self.conv], max_priority="normal")

        self.assertEqual(marked, 1)
        reminder.refresh_from_db()
        mention.refresh_from_db()
        self.assertIsNotNone(reminder.read_at)
        self.assertIsNone(mention.read_at)

    def test_noop_on_empty_or_already_read(self):
        self.assertEqual(settle_sources([]), 0)

        self._notif(self.alice, source=self.conv, read=True)
        self.assertEqual(settle_sources([self.conv]), 0)

    def test_invalidates_unread_count_cache(self):
        self._notif(self.alice, source=self.conv)
        self.assertEqual(get_unread_count(self.alice), 1)

        settle_sources([self.conv])

        self.assertEqual(get_unread_count(self.alice), 0)
