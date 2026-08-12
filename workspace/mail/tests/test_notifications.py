from unittest.mock import MagicMock, patch

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
        from datetime import timedelta

        from django.utils import timezone as dj_timezone

        return MailMessage.objects.create(
            account=self.account,
            folder=folder or self.inbox,
            imap_uid=uid,
            subject=subject,
            from_name="Bob",
            from_email="bob@example.test",
            is_read=is_read,
            date=dj_timezone.now() + timedelta(seconds=uid),
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


class SyncWiringTests(MailNotifyBase):
    """sync_folder_messages must hand the notifier a correct initial-sync flag."""

    def test_uidvalidity_reset_is_treated_as_an_initial_sync(self):
        from workspace.mail.services import imap_sync

        self.inbox.last_sync_uid = 500
        self.inbox.uid_validity = 111
        self.inbox.save(update_fields=["last_sync_uid", "uid_validity"])

        conn = MagicMock()
        conn.select.return_value = ("OK", [b""])
        conn.uid.return_value = ("OK", [b""])

        with (
            patch.object(imap_sync, "connect_imap", return_value=conn),
            patch.object(imap_sync, "_get_uidvalidity", return_value=222),
            patch.object(imap_sync, "_reconcile_folder"),
            patch.object(imap_sync, "_update_folder_counts"),
            patch(
                "workspace.mail.services.notifications.notify_new_messages"
            ) as notifier,
        ):
            imap_sync.sync_folder_messages(self.account, self.inbox)

        # The UIDVALIDITY mismatch purged the folder and reset the cursor to 0,
        # so everything is about to be re-imported: as silent as a first sync.
        self.assertTrue(notifier.called)
        self.assertTrue(notifier.call_args.kwargs["was_initial_sync"])

    def test_incremental_sync_reports_a_non_initial_sync(self):
        from workspace.mail.services import imap_sync

        self.inbox.last_sync_uid = 500
        self.inbox.uid_validity = 111
        self.inbox.save(update_fields=["last_sync_uid", "uid_validity"])

        conn = MagicMock()
        conn.select.return_value = ("OK", [b""])
        conn.uid.return_value = ("OK", [b""])

        with (
            patch.object(imap_sync, "connect_imap", return_value=conn),
            patch.object(imap_sync, "_get_uidvalidity", return_value=111),
            patch.object(imap_sync, "_reconcile_folder"),
            patch.object(imap_sync, "_update_folder_counts"),
            patch(
                "workspace.mail.services.notifications.notify_new_messages"
            ) as notifier,
        ):
            imap_sync.sync_folder_messages(self.account, self.inbox)

        self.assertTrue(notifier.called)
        self.assertFalse(notifier.call_args.kwargs["was_initial_sync"])

    def test_a_reset_that_refetches_messages_still_reports_an_initial_sync(self):
        from workspace.mail.models import MailMessage
        from workspace.mail.services import imap_sync

        self.inbox.last_sync_uid = 500
        self.inbox.uid_validity = 111
        self.inbox.save(update_fields=["last_sync_uid", "uid_validity"])

        conn = MagicMock()
        conn.select.return_value = ("OK", [b""])
        # SEARCH answers the post-reset "initial sync" branch (last_sync_uid
        # is now 0 in memory), FETCH must return a UID above 500 so max_uid
        # actually advances past 0 - otherwise this test cannot tell the
        # correct capture point from one taken after "Update sync position",
        # since both would read last_sync_uid == 0.
        conn.uid.side_effect = [
            ("OK", [b"501"]),
            ("OK", [(b"501 (UID 501 FLAGS ())", b"fake")]),
        ]
        fetched_message = MailMessage(
            account=self.account, folder=self.inbox, imap_uid=501
        )

        with (
            patch.object(imap_sync, "connect_imap", return_value=conn),
            patch.object(imap_sync, "_get_uidvalidity", return_value=222),
            patch.object(imap_sync, "_parse_message", return_value=fetched_message),
            patch.object(imap_sync, "_reconcile_folder"),
            patch.object(imap_sync, "_update_folder_counts"),
            patch(
                "workspace.mail.services.notifications.notify_new_messages"
            ) as notifier,
        ):
            imap_sync.sync_folder_messages(self.account, self.inbox)

        self.assertTrue(notifier.called)
        self.assertTrue(notifier.call_args.kwargs["was_initial_sync"])


class ClassifyWiringTests(MailNotifyBase):
    """The classify task notifies only for labels that opted in."""

    def setUp(self):
        super().setUp()
        set_setting(self.alice, "mail", "notify_mode", "labels")
        # Push dispatch is unrelated to this policy layer; stub it out so the
        # suite doesn't warn about an unconfigured VAPID key.
        self.enterContext(
            patch("workspace.notifications.tasks.send_push_notification.delay")
        )

    def run_classify(self, message, label_names, *, initial_sync=False):
        # Mirror the AITask fixture used by workspace/ai/tests/test_tasks.py -
        # read it first and copy its kwargs rather than guessing which fields
        # are required. The task object is called directly (not .delay()); a
        # bind=True Celery task supports that and binds self itself.
        from workspace.ai.models import AITask
        from workspace.ai.tasks.mail import classify_mail_messages

        if isinstance(label_names, str):
            label_names = [label_names]
        input_data = {"message_uuids": [str(message.uuid)]}
        if initial_sync:
            input_data["initial_sync"] = True
        task = AITask.objects.create(
            owner=self.alice,
            task_type=AITask.TaskType.CLASSIFY,
            input_data=input_data,
        )
        labels_json = ", ".join(f'"{name}"' for name in label_names)
        payload = f'[{{"i": 1, "labels": [{labels_json}]}}]'
        with patch(
            "workspace.ai.tasks.mail.call_llm",
            return_value={
                "content": payload,
                "model": "test",
                "prompt_tokens": 1,
                "completion_tokens": 1,
            },
        ):
            classify_mail_messages(str(task.pk))

    def test_a_notifying_label_produces_a_notification(self):
        message = self.make_message(uid=1, subject="Server down")
        self.run_classify(message, "Urgent")
        notif = Notification.objects.get(recipient=self.alice)
        self.assertEqual(notif.mail_message_id, message.pk)
        self.assertEqual(notif.priority, "high")

    def test_a_non_notifying_label_produces_nothing(self):
        message = self.make_message(uid=1, subject="Weekly digest")
        self.run_classify(message, "Newsletter")
        self.assertEqual(Notification.objects.count(), 0)

    def test_mode_all_does_not_notify_from_the_classifier(self):
        set_setting(self.alice, "mail", "notify_mode", "all")
        message = self.make_message(uid=1, subject="Server down")
        self.run_classify(message, "Urgent")
        self.assertEqual(Notification.objects.count(), 0)

    def test_initial_sync_suppresses_notifications_from_the_classifier(self):
        message = self.make_message(uid=1, subject="Server down")
        self.run_classify(message, "Urgent", initial_sync=True)
        self.assertEqual(Notification.objects.count(), 0)

    def test_two_notifying_labels_on_one_message_produce_a_single_notification(self):
        # Flip a second seeded label rather than creating a new one, so the
        # fixture stays close to real account data.
        MailLabel.objects.filter(account=self.account, name="Action").update(
            notify_on_apply=True
        )
        message = self.make_message(uid=1, subject="Server down")
        self.run_classify(message, ["Urgent", "Action"])
        self.assertEqual(Notification.objects.count(), 1)

    def test_the_message_queryset_avoids_per_message_folder_queries(self):
        from workspace.ai.tasks.mail import _classify_message_queryset

        messages = [self.make_message(uid=i) for i in range(3)]
        uuids = [str(m.uuid) for m in messages]

        with self.assertNumQueries(1):
            fetched = list(_classify_message_queryset(self.alice, uuids))

        with self.assertNumQueries(0):
            for m in fetched:
                self.assertIsNotNone(m.folder.folder_type)
                self.assertFalse(m.folder.is_hidden)


class MessageListMarksNotificationsReadTests(MailNotifyBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.alice)

    def make_notification(self, message):
        return Notification.objects.create(
            recipient=self.alice,
            origin="mail",
            icon="",
            title="Hi",
            mail_message=message,
        )

    def test_listing_a_folder_marks_its_notifications_read(self):
        message = self.make_message(uid=1)
        notif = self.make_notification(message)
        resp = self.client.get(f"/api/v1/mail/messages?folder={self.inbox.uuid}")
        self.assertEqual(resp.status_code, 200)
        notif.refresh_from_db()
        self.assertIsNotNone(notif.read_at)

    def test_the_unified_inbox_view_marks_them_too(self):
        message = self.make_message(uid=1)
        notif = self.make_notification(message)
        resp = self.client.get("/api/v1/mail/messages?inbox=all")
        self.assertEqual(resp.status_code, 200)
        notif.refresh_from_db()
        self.assertIsNotNone(notif.read_at)

    def test_a_message_on_another_page_keeps_its_notification(self):
        # page_size is 50; only the first page is marked.
        messages = [self.make_message(uid=i) for i in range(60)]
        oldest = min(messages, key=lambda m: m.imap_uid)
        notif = self.make_notification(oldest)
        self.client.get(f"/api/v1/mail/messages?folder={self.inbox.uuid}&page=1")
        notif.refresh_from_db()
        self.assertIsNone(notif.read_at)


class MailIndexEmbedsNotificationPrefsTests(MailNotifyBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.alice)

    def test_the_resolved_mode_and_burst_are_embedded(self):
        set_setting(self.alice, "mail", "notify_mode", "all")
        set_setting(self.alice, "mail", "notify_max_burst", 999999)
        resp = self.client.get("/mail")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["notify_mode"], "all")
        # Embedded already clamped, so the select cannot show an unrunnable value.
        self.assertEqual(resp.context["notify_max_burst"], HARD_MAX_NOTIFY_BURST)

    def test_the_page_renders_both_json_script_blocks(self):
        html = self.client.get("/mail").content.decode()
        self.assertIn('id="mail-notify-mode-data"', html)
        self.assertIn('id="mail-notify-burst-data"', html)
