from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.chat.models import Conversation
from workspace.notifications.models import Notification
from workspace.notifications.services.notifications import mark_source_read

User = get_user_model()


@patch("workspace.notifications.services.notifications.notify_sse")
class MarkSourceReadTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.conv = Conversation.objects.create(created_by=self.alice)
        self.other = Conversation.objects.create(created_by=self.alice)

    def tearDown(self):
        cache.clear()

    def _notif(self, recipient, conversation):
        return Notification.objects.create(
            recipient=recipient,
            origin="chat",
            icon="",
            title="t",
            conversation=conversation,
        )

    def test_marks_unread_for_user_and_source(self, mock_sse):
        n = self._notif(self.alice, self.conv)
        marked = mark_source_read(self.alice, self.conv)
        self.assertEqual(marked, 1)
        n.refresh_from_db()
        self.assertIsNotNone(n.read_at)
        mock_sse.assert_called_with("notifications", self.alice.pk)

    def test_other_users_and_sources_untouched(self, mock_sse):
        bob_n = self._notif(self.bob, self.conv)
        other_n = self._notif(self.alice, self.other)
        mark_source_read(self.alice, self.conv)
        bob_n.refresh_from_db()
        other_n.refresh_from_db()
        self.assertIsNone(bob_n.read_at)
        self.assertIsNone(other_n.read_at)

    def test_accepts_unsaved_instance_with_pk(self, mock_sse):
        n = self._notif(self.alice, self.conv)
        marked = mark_source_read(self.alice, Conversation(pk=self.conv.pk))
        self.assertEqual(marked, 1)
        n.refresh_from_db()
        self.assertIsNotNone(n.read_at)

    def test_nothing_to_mark_skips_sse(self, mock_sse):
        marked = mark_source_read(self.alice, self.conv)
        self.assertEqual(marked, 0)
        mock_sse.assert_not_called()
