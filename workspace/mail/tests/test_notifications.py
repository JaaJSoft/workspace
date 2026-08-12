from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailLabel, MailMessage
from workspace.mail.services.notifications import (
    DEFAULT_NOTIFY_BURST,
    HARD_MAX_NOTIFY_BURST,
    notify_labeled_messages,
    notify_new_messages,
    resolve_notify_burst,
    resolve_notify_mode,
)
from workspace.notifications.models import Notification
from workspace.users.services.settings import set_setting

User = get_user_model()


class MailNotifyBase(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="notifmail", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.alice,
            email="a@example.test",
            imap_host="imap.example.test",
            smtp_host="smtp.example.test",
            username="a@example.test",
        )
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )

    def tearDown(self):
        # LocMemCache is process-global and is not reset between TestCase runs.
        cache.clear()

    def make_message(self, *, uid, subject="Hi", is_read=False, folder=None):
        return MailMessage.objects.create(
            account=self.account,
            folder=folder or self.inbox,
            imap_uid=uid,
            subject=subject,
            from_name="Bob",
            from_email="bob@example.test",
            is_read=is_read,
        )


class ResolveNotifyModeTests(MailNotifyBase):
    def test_explicit_mode_is_honoured(self):
        set_setting(self.alice, "mail", "notify_mode", "all")
        self.assertEqual(resolve_notify_mode(self.alice), "all")

    def test_unrecognised_mode_falls_back_to_the_computed_default(self):
        set_setting(self.alice, "mail", "notify_mode", "sometimes")
        with patch("workspace.ai.client.is_ai_enabled", return_value=False):
            self.assertEqual(resolve_notify_mode(self.alice), "never")

    def test_default_is_labels_when_the_classifier_can_run(self):
        with patch("workspace.ai.client.is_ai_enabled", return_value=True):
            self.assertEqual(resolve_notify_mode(self.alice), "labels")

    def test_default_is_never_without_a_server_side_key(self):
        with patch("workspace.ai.client.is_ai_enabled", return_value=False):
            self.assertEqual(resolve_notify_mode(self.alice), "never")

    def test_default_is_never_when_the_user_disabled_auto_classify(self):
        set_setting(self.alice, "mail", "ai_classify", False)
        with patch("workspace.ai.client.is_ai_enabled", return_value=True):
            self.assertEqual(resolve_notify_mode(self.alice), "never")


class ResolveNotifyBurstTests(MailNotifyBase):
    def test_missing_setting_uses_the_default(self):
        self.assertEqual(resolve_notify_burst(self.alice), DEFAULT_NOTIFY_BURST)

    def test_stored_integer_is_honoured(self):
        set_setting(self.alice, "mail", "notify_max_burst", 5)
        self.assertEqual(resolve_notify_burst(self.alice), 5)

    def test_numeric_string_is_coerced(self):
        set_setting(self.alice, "mail", "notify_max_burst", "20")
        self.assertEqual(resolve_notify_burst(self.alice), 20)

    def test_garbage_falls_back_to_the_default(self):
        for bad in ("abc", None, [], {}):
            with self.subTest(value=bad):
                set_setting(self.alice, "mail", "notify_max_burst", bad)
                self.assertEqual(resolve_notify_burst(self.alice), DEFAULT_NOTIFY_BURST)

    def test_zero_and_negatives_clamp_up_to_one(self):
        for bad in (0, -1, -999):
            with self.subTest(value=bad):
                set_setting(self.alice, "mail", "notify_max_burst", bad)
                self.assertEqual(resolve_notify_burst(self.alice), 1)

    def test_absurd_values_clamp_down_to_the_ceiling(self):
        set_setting(self.alice, "mail", "notify_max_burst", 999999)
        self.assertEqual(resolve_notify_burst(self.alice), HARD_MAX_NOTIFY_BURST)

    def test_infinite_values_fall_back_to_the_default(self):
        # Not stored via set_setting: JSONField round-trips a raw float
        # infinity inconsistently across backends (SQLite accepts the
        # non-standard "Infinity" token, Postgres JSONB rejects it), so this
        # goes straight at int()'s OverflowError instead of through storage.
        for bad in (float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with patch(
                    "workspace.mail.services.notifications.get_module_settings",
                    return_value={"notify_max_burst": bad},
                ):
                    self.assertEqual(
                        resolve_notify_burst(self.alice), DEFAULT_NOTIFY_BURST
                    )


class NotifyNewMessagesTests(MailNotifyBase):
    def setUp(self):
        super().setUp()
        set_setting(self.alice, "mail", "notify_mode", "all")
        # Push dispatch is unrelated to this policy layer; stub it out so the
        # suite doesn't warn about an unconfigured VAPID key.
        self.enterContext(
            patch("workspace.notifications.tasks.send_push_notification.delay")
        )

    def notify(self, messages, *, was_initial_sync=False, folder=None):
        return notify_new_messages(
            folder or self.inbox,
            [str(m.uuid) for m in messages],
            was_initial_sync=was_initial_sync,
        )

    def test_notifies_one_per_unread_message(self):
        messages = [self.make_message(uid=i) for i in range(3)]
        self.assertEqual(self.notify(messages), 3)
        self.assertEqual(Notification.objects.filter(recipient=self.alice).count(), 3)

    def test_notification_points_at_the_message(self):
        message = self.make_message(uid=1, subject="Invoice")
        self.notify([message])
        notif = Notification.objects.get(recipient=self.alice)
        self.assertEqual(notif.mail_message_id, message.pk)
        self.assertEqual(notif.origin, "mail")
        self.assertEqual(notif.title, "Bob")
        self.assertEqual(notif.body, "Invoice")
        self.assertEqual(notif.url, f"/mail?message={message.uuid}")
        self.assertEqual(notif.priority, "normal")
        # An external sender has no workspace account to attribute this to.
        self.assertIsNone(notif.actor_id)

    def test_skips_messages_that_arrived_already_read(self):
        self.assertEqual(self.notify([self.make_message(uid=1, is_read=True)]), 0)

    def test_skips_sent_and_drafts(self):
        for folder_type in ("sent", "drafts"):
            with self.subTest(folder_type=folder_type):
                folder = MailFolder.objects.create(
                    account=self.account,
                    name=folder_type,
                    display_name=folder_type,
                    folder_type=folder_type,
                )
                message = self.make_message(uid=100, folder=folder)
                self.assertEqual(self.notify([message], folder=folder), 0)

    def test_skips_hidden_folders(self):
        self.inbox.is_hidden = True
        self.inbox.save(update_fields=["is_hidden"])
        self.assertEqual(self.notify([self.make_message(uid=1)]), 0)

    def test_skips_the_initial_sync_entirely(self):
        messages = [self.make_message(uid=i) for i in range(5)]
        self.assertEqual(self.notify(messages, was_initial_sync=True), 0)

    def test_stops_at_the_burst_limit(self):
        set_setting(self.alice, "mail", "notify_max_burst", 3)
        messages = [self.make_message(uid=i) for i in range(7)]
        self.assertEqual(self.notify(messages), 3)

    def test_logs_what_the_burst_limit_dropped(self):
        set_setting(self.alice, "mail", "notify_max_burst", 2)
        messages = [self.make_message(uid=i) for i in range(6)]
        with self.assertLogs("workspace.mail.services.notifications", "INFO") as logs:
            self.notify(messages)
        self.assertTrue(any("2 of 6" in line for line in logs.output))

    def test_does_nothing_in_the_other_modes(self):
        for mode in ("labels", "never"):
            with self.subTest(mode=mode):
                Notification.objects.all().delete()
                set_setting(self.alice, "mail", "notify_mode", mode)
                message = self.make_message(uid=1)
                self.assertEqual(self.notify([message]), 0)
                message.delete()


class NotifyLabeledMessagesTests(MailNotifyBase):
    def setUp(self):
        super().setUp()
        set_setting(self.alice, "mail", "notify_mode", "labels")
        self.urgent = MailLabel.objects.get(account=self.account, name="Urgent")
        # Push dispatch is unrelated to this policy layer; stub it out so the
        # suite doesn't warn about an unconfigured VAPID key.
        self.enterContext(
            patch("workspace.notifications.tasks.send_push_notification.delay")
        )

    def test_notifies_at_high_priority(self):
        message = self.make_message(uid=1, subject="Server down")
        self.assertEqual(
            notify_labeled_messages(self.alice, [message], was_initial_sync=False), 1
        )
        notif = Notification.objects.get(recipient=self.alice)
        self.assertEqual(notif.priority, "high")
        self.assertEqual(notif.mail_message_id, message.pk)

    def test_skips_already_read_messages(self):
        message = self.make_message(uid=1, is_read=True)
        self.assertEqual(
            notify_labeled_messages(self.alice, [message], was_initial_sync=False), 0
        )

    def test_skips_hidden_folders(self):
        self.inbox.is_hidden = True
        self.inbox.save(update_fields=["is_hidden"])
        message = self.make_message(uid=1)
        message.refresh_from_db()
        self.assertEqual(
            notify_labeled_messages(self.alice, [message], was_initial_sync=False), 0
        )

    def test_skips_the_initial_sync(self):
        message = self.make_message(uid=1)
        self.assertEqual(
            notify_labeled_messages(self.alice, [message], was_initial_sync=True), 0
        )

    def test_stops_at_the_burst_limit(self):
        set_setting(self.alice, "mail", "notify_max_burst", 2)
        messages = [self.make_message(uid=i) for i in range(5)]
        self.assertEqual(
            notify_labeled_messages(self.alice, messages, was_initial_sync=False), 2
        )

    def test_does_nothing_in_the_other_modes(self):
        for mode in ("all", "never"):
            with self.subTest(mode=mode):
                Notification.objects.all().delete()
                set_setting(self.alice, "mail", "notify_mode", mode)
                message = self.make_message(uid=1)
                self.assertEqual(
                    notify_labeled_messages(
                        self.alice, [message], was_initial_sync=False
                    ),
                    0,
                )
                message.delete()
