from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase

from workspace.chat.models import Conversation
from workspace.notifications.models import Notification
from workspace.notifications.services.notifications import (
    get_unread_count,
    mark_source_read,
    notify,
    notify_many,
    settle_sources,
)

User = get_user_model()


class NotifyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username="alice", password="pass")

    def tearDown(self):
        cache.clear()

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_creates_notification(self, mock_sse, mock_push):
        notif = notify(
            recipient=self.alice,
            origin="chat",
            title="New message",
            body="Hello",
            url="/chat/123",
        )
        self.assertEqual(notif.recipient, self.alice)
        self.assertEqual(notif.title, "New message")
        self.assertEqual(Notification.objects.count(), 1)

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_triggers_sse(self, mock_sse, mock_push):
        with self.captureOnCommitCallbacks(execute=True):
            notify(recipient=self.alice, origin="chat", title="Test")
        mock_sse.assert_called_with("notifications", self.alice.id)

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_triggers_push_for_normal_priority(self, mock_sse, mock_push):
        with self.captureOnCommitCallbacks(execute=True):
            notify(recipient=self.alice, origin="chat", title="Test")
        mock_push.delay.assert_called_once()

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_skips_push_for_low_priority(self, mock_sse, mock_push):
        with self.captureOnCommitCallbacks(execute=True):
            notify(recipient=self.alice, origin="chat", title="Test", priority="low")
        mock_push.delay.assert_not_called()

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_invalidates_unread_cache(self, mock_sse, mock_push):
        # Warm the cache with the pre-notify count
        self.assertEqual(get_unread_count(self.alice), 0)
        notify(recipient=self.alice, origin="chat", title="Test")
        # After notify, the cache must reflect the new unread count
        self.assertEqual(get_unread_count(self.alice), 1)

    def test_notification_has_no_stored_color(self):
        from workspace.notifications.models import Notification

        field_names = {f.name for f in Notification._meta.get_fields()}
        self.assertNotIn("color", field_names)


class NotifyManyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")

    def tearDown(self):
        cache.clear()

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_creates_notifications_for_all_recipients(self, mock_sse, mock_push):
        notifs = notify_many(
            recipients=[self.alice, self.bob],
            origin="files",
            title="Shared",
            body="File shared",
        )
        self.assertEqual(len(notifs), 2)
        self.assertEqual(Notification.objects.count(), 2)

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_triggers_sse_for_each_recipient(self, mock_sse, mock_push):
        with self.captureOnCommitCallbacks(execute=True):
            notify_many(
                recipients=[self.alice, self.bob],
                origin="files",
                title="Shared",
            )
        self.assertEqual(mock_sse.call_count, 2)

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_skips_push_for_low_priority(self, mock_sse, mock_push):
        with self.captureOnCommitCallbacks(execute=True):
            notify_many(
                recipients=[self.alice, self.bob],
                origin="files",
                title="Shared",
                priority="low",
            )
        mock_push.delay.assert_not_called()


class GetUnreadCountTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username="alice", password="pass")

    def tearDown(self):
        cache.clear()

    def test_returns_zero_when_no_notifications(self):
        self.assertEqual(get_unread_count(self.alice), 0)

    def test_counts_unread_notifications(self):
        Notification.objects.create(
            recipient=self.alice,
            origin="chat",
            icon="msg",
            title="Test1",
        )
        Notification.objects.create(
            recipient=self.alice,
            origin="chat",
            icon="msg",
            title="Test2",
        )
        self.assertEqual(get_unread_count(self.alice), 2)

    def test_excludes_read_notifications(self):
        from django.utils import timezone

        Notification.objects.create(
            recipient=self.alice,
            origin="chat",
            icon="msg",
            title="Read",
            read_at=timezone.now(),
        )
        self.assertEqual(get_unread_count(self.alice), 0)

    def test_caches_result(self):
        self.assertEqual(get_unread_count(self.alice), 0)
        # Add a notification — count should still be cached as 0
        Notification.objects.create(
            recipient=self.alice,
            origin="chat",
            icon="msg",
            title="New",
        )
        self.assertEqual(get_unread_count(self.alice), 0)  # cached


class NotifySourceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username="alice", password="pass")

    def tearDown(self):
        cache.clear()

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_notify_sets_source_fk(self, mock_sse, mock_push):
        from workspace.chat.models import Conversation

        conv = Conversation.objects.create(created_by=self.alice)
        notif = notify(recipient=self.alice, origin="chat", title="Hi", source=conv)
        self.assertEqual(notif.conversation_id, conv.pk)

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_notify_many_sets_source_fk(self, mock_sse, mock_push):
        from workspace.files.models import File

        file_obj = File.objects.create(owner=self.alice, name="a.txt", node_type="file")
        notifs = notify_many(
            recipients=[self.alice], origin="files", title="Hi", source=file_obj
        )
        self.assertEqual(notifs[0].file_id, file_obj.pk)

    def test_unmapped_source_raises(self):
        with self.assertRaises(ValueError):
            notify(
                recipient=self.alice,
                origin="core",
                title="Hi",
                source=self.alice,  # User is not a notification source
            )


class MarkSourcesReadTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="marksrcs", password="pass")
        self.conv_a = Conversation.objects.create(created_by=self.alice, kind="dm")
        self.conv_b = Conversation.objects.create(created_by=self.alice, kind="dm")

    def make(self, conversation):
        return Notification.objects.create(
            recipient=self.alice,
            origin="chat",
            icon="",
            title="Hi",
            conversation=conversation,
        )

    def test_marks_every_listed_source(self):
        from workspace.notifications.services.notifications import mark_sources_read

        self.make(self.conv_a)
        self.make(self.conv_b)
        self.assertEqual(mark_sources_read(self.alice, [self.conv_a, self.conv_b]), 2)
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.alice, read_at__isnull=True
            ).count(),
            0,
        )

    def test_leaves_unlisted_sources_alone(self):
        from workspace.notifications.services.notifications import mark_sources_read

        self.make(self.conv_a)
        kept = self.make(self.conv_b)
        self.assertEqual(mark_sources_read(self.alice, [self.conv_a]), 1)
        kept.refresh_from_db()
        self.assertIsNone(kept.read_at)

    def test_empty_source_list_is_a_no_op(self):
        from workspace.notifications.services.notifications import mark_sources_read

        self.assertEqual(mark_sources_read(self.alice, []), 0)


@patch("workspace.notifications.services.notifications.send_push_notification")
@patch("workspace.notifications.services.notifications.notify_sse")
class AnnounceAfterCommitTests(TestCase):
    """The stream wake-up and the unread cache wait for the rows to commit.

    A client woken, or a count read, before the commit sees the old rows; the
    count would then be cached under the new version and served until the TTL.
    """

    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.conv = Conversation.objects.create(created_by=self.alice)

    def tearDown(self):
        cache.clear()

    def test_notify_wakes_the_stream_after_commit(self, mock_sse, mock_push):
        with self.captureOnCommitCallbacks(execute=True):
            notify(recipient=self.alice, origin="chat", title="Test")
            mock_sse.assert_not_called()
        mock_sse.assert_called_once_with("notifications", self.alice.id)

    def test_rolled_back_notify_wakes_nobody(self, mock_sse, mock_push):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    notify(recipient=self.alice, origin="chat", title="Test")
                    raise RuntimeError
            except RuntimeError:
                pass
        mock_sse.assert_not_called()

    def test_count_cached_before_commit_is_evicted_on_commit(self, mock_sse, mock_push):
        with self.captureOnCommitCallbacks(execute=True):
            notify(recipient=self.alice, origin="chat", title="Test")
            # A concurrent request does not see the uncommitted row yet and
            # caches the count it does see. Hiding the row for that one read
            # stands in for the other connection's isolation.
            Notification.objects.update(read_at="2026-01-01T00:00:00Z")
            self.assertEqual(get_unread_count(self.alice), 0)
            Notification.objects.update(read_at=None)
        self.assertEqual(get_unread_count(self.alice), 1)

    def test_read_in_the_same_transaction_sees_the_new_row(self, mock_sse, mock_push):
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(get_unread_count(self.alice), 0)
            notify(recipient=self.alice, origin="chat", title="Test")
            self.assertEqual(get_unread_count(self.alice), 1)

    def test_mark_source_read_wakes_the_stream_after_commit(self, mock_sse, mock_push):
        Notification.objects.create(
            recipient=self.alice, origin="chat", title="t", conversation=self.conv
        )
        with self.captureOnCommitCallbacks(execute=True):
            mark_source_read(self.alice, self.conv)
            mock_sse.assert_not_called()
        mock_sse.assert_called_once_with("notifications", self.alice.pk)

    def test_settle_sources_wakes_the_stream_after_commit(self, mock_sse, mock_push):
        Notification.objects.create(
            recipient=self.alice, origin="chat", title="t", conversation=self.conv
        )
        with self.captureOnCommitCallbacks(execute=True):
            settle_sources([self.conv])
            mock_sse.assert_not_called()
        mock_sse.assert_called_once_with("notifications", self.alice.pk)
