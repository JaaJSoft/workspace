from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class NotifyPushIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="intuser",
            email="int@test.com",
            password="pass123",
        )

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_notify_dispatches_push_for_normal_priority(self, _mock_sse, mock_push):
        from workspace.notifications.services.notifications import notify

        with self.captureOnCommitCallbacks(execute=True):
            notif = notify(
                recipient=self.user,
                origin="test",
                title="Normal notification",
                priority="normal",
            )
        mock_push.delay.assert_called_once_with(str(notif.uuid))

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_notify_skips_push_for_low_priority(self, _mock_sse, mock_push):
        from workspace.notifications.services.notifications import notify

        with self.captureOnCommitCallbacks(execute=True):
            notify(
                recipient=self.user,
                origin="test",
                title="Low notification",
                priority="low",
            )
        mock_push.delay.assert_not_called()

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_notify_many_dispatches_push_per_recipient(self, _mock_sse, mock_push):
        user2 = User.objects.create_user(
            username="intuser2",
            email="int2@test.com",
            password="pass123",
        )
        from workspace.notifications.services.notifications import notify_many

        with self.captureOnCommitCallbacks(execute=True):
            notifs = notify_many(
                recipients=[self.user, user2],
                origin="test",
                title="Batch notification",
                priority="normal",
            )
        self.assertEqual(
            sorted(c.args[0] for c in mock_push.delay.call_args_list),
            sorted(str(n.uuid) for n in notifs),
        )

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_notify_many_skips_push_for_low(self, _mock_sse, mock_push):
        user2 = User.objects.create_user(
            username="intuser2",
            email="int2@test.com",
            password="pass123",
        )
        from workspace.notifications.services.notifications import notify_many

        with self.captureOnCommitCallbacks(execute=True):
            notify_many(
                recipients=[self.user, user2],
                origin="test",
                title="Low batch",
                priority="low",
            )
        mock_push.delay.assert_not_called()

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_notify_push_waits_for_commit(self, _mock_sse, mock_push):
        # A worker picking the task up before the row is committed hits
        # Notification.DoesNotExist and drops the push without a trace.
        from workspace.notifications.services.notifications import notify

        with self.captureOnCommitCallbacks(execute=True):
            notify(recipient=self.user, origin="test", title="In a transaction")
            mock_push.delay.assert_not_called()
        mock_push.delay.assert_called_once()

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_notify_many_push_waits_for_commit(self, _mock_sse, mock_push):
        user2 = User.objects.create_user(username="intuser2", password="pass123")
        from workspace.notifications.services.notifications import notify_many

        with self.captureOnCommitCallbacks(execute=True):
            notify_many(
                recipients=[self.user, user2], origin="test", title="In a transaction"
            )
            mock_push.delay.assert_not_called()
        self.assertEqual(mock_push.delay.call_count, 2)

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_rolled_back_notify_sends_no_push(self, _mock_sse, mock_push):
        from django.db import transaction

        from workspace.notifications.models import Notification
        from workspace.notifications.services.notifications import notify

        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    notify(recipient=self.user, origin="test", title="Rolled back")
                    raise RuntimeError
            except RuntimeError:
                pass
        mock_push.delay.assert_not_called()
        self.assertFalse(Notification.objects.exists())

    @patch("workspace.notifications.services.notifications.send_push_notification")
    @patch("workspace.notifications.services.notifications.notify_sse")
    def test_notify_many_failing_dispatch_does_not_block_the_others(
        self, _mock_sse, mock_push
    ):
        user2 = User.objects.create_user(username="intuser2", password="pass123")
        from workspace.notifications.services.notifications import notify_many

        mock_push.delay.side_effect = [Exception("broker down"), None]
        # captureOnCommitCallbacks reports robust-callback failures on the
        # django.test logger (the runtime path uses django.db.backends.base).
        with (
            self.assertLogs("django.test", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            notify_many(recipients=[self.user, user2], origin="test", title="x")
        self.assertEqual(mock_push.delay.call_count, 2)
