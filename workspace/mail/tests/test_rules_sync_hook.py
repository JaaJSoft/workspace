from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.imap_sync import sync_folder_messages
from workspace.mail.tests.imap_fake import fake_imap
from workspace.notifications.models import Notification
from workspace.users.services.settings import set_setting

User = get_user_model()


def _sync_one_new_message(account, folder):
    """Sync `folder` with one message waiting on the server; returns its row."""
    with fake_imap() as server:
        uid = server.deliver(folder.name, uid=folder.last_sync_uid + 1)
        sync_folder_messages(account, folder)
    return MailMessage.objects.get(folder=folder, imap_uid=uid)


class SyncRulesHookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="shu", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="sh@x.com",
            imap_host="x",
            smtp_host="x",
            username="sh@x.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            folder_type="inbox",
        )

    @patch("workspace.mail.services.rules.engine.run_rules_for_messages")
    @patch("workspace.ai.services.dispatch.dispatch")
    @patch("workspace.users.services.settings.get_setting", return_value=True)
    @patch("workspace.ai.client.is_ai_enabled", return_value=True)
    @patch("workspace.calendar.services.ics_processor.process_calendar_emails")
    def test_rules_invoked_for_new_inbox_message(
        self,
        _mock_proc,
        _mock_ai,
        _mock_setting,
        _mock_dispatch,
        mock_rules,
    ):
        new_msg = _sync_one_new_message(self.account, self.folder)

        mock_rules.assert_called_once()
        args, _ = mock_rules.call_args
        self.assertEqual(args[0], self.account)
        self.assertIn(str(new_msg.uuid), args[1])

    @patch("workspace.mail.services.rules.engine.run_rules_for_messages")
    @patch("workspace.ai.services.dispatch.dispatch")
    @patch("workspace.users.services.settings.get_setting", return_value=True)
    @patch("workspace.ai.client.is_ai_enabled", return_value=True)
    @patch("workspace.calendar.services.ics_processor.process_calendar_emails")
    def test_rules_not_invoked_for_sent_folder(
        self,
        _mock_proc,
        _mock_ai,
        _mock_setting,
        _mock_dispatch,
        mock_rules,
    ):
        sent = MailFolder.objects.create(
            account=self.account,
            name="Sent",
            folder_type="sent",
        )

        _sync_one_new_message(self.account, sent)

        mock_rules.assert_not_called()

    @patch(
        "workspace.mail.services.rules.engine.run_rules_for_messages",
        side_effect=Exception("rule blew up"),
    )
    @patch("workspace.ai.services.dispatch.dispatch")
    @patch("workspace.users.services.settings.get_setting", return_value=True)
    @patch("workspace.ai.client.is_ai_enabled", return_value=True)
    @patch("workspace.calendar.services.ics_processor.process_calendar_emails")
    def test_rules_exception_does_not_break_sync(
        self,
        _mock_proc,
        _mock_ai,
        _mock_setting,
        mock_dispatch,
        _mock_rules,
    ):
        """A misbehaving rule must never abort the sync - the AI dispatch
        and the rest of the sync pipeline still run."""
        with self.assertLogs("workspace.mail.services.imap_sync", level="ERROR"):
            # No exception propagates.
            _sync_one_new_message(self.account, self.folder)

        # AI dispatch still ran for the new message.
        self.assertTrue(mock_dispatch.called)


class NotifyRunsAfterRulesTests(TestCase):
    """notify_new_messages sits after the rules block precisely so its
    re-query (is_read=False, deleted_at__isnull=True) picks up whatever a
    rule just did to the same new message."""

    def setUp(self):
        self.user = User.objects.create_user(username="nar", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="nar@x.com",
            imap_host="x",
            smtp_host="x",
            username="nar@x.com",
        )
        # last_sync_uid > 0 so was_initial_sync is False: notify_new_messages
        # short-circuits unconditionally on an initial sync, which would
        # make this test pass whether or not notify actually runs after
        # the rules block.
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            folder_type="inbox",
            last_sync_uid=1,
        )
        set_setting(self.user, "mail", "notify_mode", "all")
        self.enterContext(
            patch("workspace.notifications.tasks.send_push_notification.delay")
        )
        self.enterContext(patch("workspace.ai.services.dispatch.dispatch"))
        self.enterContext(patch("workspace.ai.client.is_ai_enabled", return_value=True))
        self.enterContext(
            patch("workspace.calendar.services.ics_processor.process_calendar_emails")
        )

    def tearDown(self):
        cache.clear()

    def _sync_with_rule(self, rule):
        with patch(
            "workspace.mail.services.rules.engine.run_rules_for_messages",
            side_effect=rule,
        ):
            return _sync_one_new_message(self.account, self.folder)

    def test_a_message_no_rule_touches_is_notified(self):
        """The control of the two tests below: without it they would pass on
        a sync that never notifies at all."""
        new_msg = self._sync_with_rule(lambda account, message_uuids: None)

        self.assertEqual(Notification.objects.filter(mail_message=new_msg).count(), 1)

    def test_a_rule_deleted_message_is_not_notified(self):
        def delete_it(account, message_uuids):
            MailMessage.objects.filter(uuid__in=message_uuids).update(
                deleted_at=timezone.now()
            )

        new_msg = self._sync_with_rule(delete_it)

        self.assertIsNotNone(new_msg.deleted_at)
        self.assertEqual(Notification.objects.count(), 0)

    def test_a_rule_marked_read_message_is_not_notified(self):
        def mark_read_it(account, message_uuids):
            MailMessage.objects.filter(uuid__in=message_uuids).update(is_read=True)

        new_msg = self._sync_with_rule(mark_read_it)

        self.assertTrue(new_msg.is_read)
        self.assertEqual(Notification.objects.count(), 0)
