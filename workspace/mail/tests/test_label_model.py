from django.contrib.auth import get_user_model
from django.db import IntegrityError, migrations
from django.test import TestCase

from workspace.mail.models import (
    MailAccount,
    MailFolder,
    MailLabel,
    MailMessage,
    MailMessageLabel,
)

User = get_user_model()


class MailLabelModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="labeluser", password="pass123")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="label@test.com",
            imap_host="imap.test.com",
            smtp_host="smtp.test.com",
            username="label@test.com",
        )

    def test_create_label(self):
        label = MailLabel.objects.create(
            account=self.account,
            name="Custom1",
            color="error",
            icon="alert-triangle",
        )
        self.assertEqual(label.name, "Custom1")
        self.assertEqual(label.color, "error")
        self.assertEqual(label.account, self.account)
        self.assertEqual(label.position, 0)

    def test_unique_name_per_account(self):
        MailLabel.objects.create(account=self.account, name="Duplicate")
        with self.assertRaises(IntegrityError):
            MailLabel.objects.create(account=self.account, name="Duplicate")

    def test_same_name_different_accounts(self):
        user2 = User.objects.create_user(username="labeluser2", password="pass123")
        account2 = MailAccount.objects.create(
            owner=user2,
            email="label2@test.com",
            imap_host="imap.test.com",
            smtp_host="smtp.test.com",
            username="label2@test.com",
        )
        MailLabel.objects.create(account=self.account, name="Custom")
        MailLabel.objects.create(account=account2, name="Custom")
        self.assertEqual(MailLabel.objects.filter(name="Custom").count(), 2)

    def test_ordering(self):
        MailLabel.objects.create(account=self.account, name="Zebra", position=20)
        MailLabel.objects.create(account=self.account, name="Alpha", position=10)
        MailLabel.objects.create(account=self.account, name="Beta", position=10)
        labels = list(
            MailLabel.objects.filter(
                account=self.account, position__gte=10
            ).values_list("name", flat=True)
        )
        self.assertEqual(labels, ["Alpha", "Beta", "Zebra"])

    def test_cascade_delete_account(self):
        MailLabel.objects.create(account=self.account, name="Custom")
        account_uuid = self.account.uuid
        self.account.delete()
        self.assertEqual(MailLabel.objects.filter(account_id=account_uuid).count(), 0)


class MailMessageLabelModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mmluser", password="pass123")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="mml@test.com",
            imap_host="imap.test.com",
            smtp_host="smtp.test.com",
            username="mml@test.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.msg = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
            subject="Test",
        )
        # Use a custom label to avoid collision with seeded defaults
        self.label = MailLabel.objects.create(
            account=self.account,
            name="CustomTag",
            color="accent",
        )

    def test_assign_label_to_message(self):
        link = MailMessageLabel.objects.create(message=self.msg, label=self.label)
        self.assertEqual(link.message, self.msg)
        self.assertEqual(link.label, self.label)

    def test_unique_message_label(self):
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        with self.assertRaises(IntegrityError):
            MailMessageLabel.objects.create(message=self.msg, label=self.label)

    def test_cascade_delete_message(self):
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        self.msg.delete()
        self.assertEqual(MailMessageLabel.objects.count(), 0)

    def test_cascade_delete_label(self):
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        self.label.delete()
        self.assertEqual(MailMessageLabel.objects.count(), 0)

    def test_message_labels_reverse(self):
        label2 = MailLabel.objects.create(account=self.account, name="FYI2")
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        MailMessageLabel.objects.create(message=self.msg, label=label2)
        self.assertEqual(self.msg.message_labels.count(), 2)

    def test_label_links_reverse(self):
        msg2 = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=2,
            subject="Test2",
        )
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        MailMessageLabel.objects.create(message=msg2, label=self.label)
        self.assertEqual(self.label.label_links.count(), 2)


class DefaultLabelSeedTests(TestCase):
    def test_new_account_gets_default_labels(self):
        user = User.objects.create_user(username="seeduser", password="pass123")
        account = MailAccount.objects.create(
            owner=user,
            email="seed@test.com",
            imap_host="imap.test.com",
            smtp_host="smtp.test.com",
            username="seed@test.com",
        )
        labels = list(
            account.labels.order_by("position").values_list("name", flat=True)
        )
        self.assertEqual(
            labels, ["Urgent", "Action", "FYI", "Newsletter", "Notification"]
        )

    def test_save_existing_account_does_not_duplicate(self):
        user = User.objects.create_user(username="seeduser2", password="pass123")
        account = MailAccount.objects.create(
            owner=user,
            email="seed2@test.com",
            imap_host="imap.test.com",
            smtp_host="smtp.test.com",
            username="seed2@test.com",
        )
        account.display_name = "Updated"
        account.save()
        self.assertEqual(account.labels.count(), 5)

    def test_signal_skips_seeding_when_raw(self):
        """Regression: loaddata fires post_save with raw=True; the handler must
        skip label seeding so the fixture-provided labels do not collide with
        signal-created duplicates.
        """
        from workspace.mail.signals import seed_default_labels

        user = User.objects.create_user(username="rawuser", password="pass123")
        # Build an unsaved account; the signal should be a no-op.
        account = MailAccount(
            owner=user,
            email="raw@test.com",
            imap_host="imap.test.com",
            smtp_host="smtp.test.com",
            username="raw@test.com",
        )

        before = MailLabel.objects.count()
        seed_default_labels(
            sender=MailAccount,
            instance=account,
            created=True,
            raw=True,
            using="default",
        )
        self.assertEqual(MailLabel.objects.count(), before)


class LabelNotifyOnApplySeedTests(TestCase):
    def test_seeding_flags_urgent_only(self):
        from workspace.mail.models import MailAccount, MailLabel

        account = MailAccount.objects.create(
            owner=User.objects.create_user(username="seedflag", password="pass"),
            email="s@example.test",
            imap_host="imap.example.test",
            smtp_host="smtp.example.test",
            username="s@example.test",
        )
        flagged = set(
            MailLabel.objects.filter(account=account, notify_on_apply=True).values_list(
                "name", flat=True
            )
        )
        self.assertEqual(flagged, {"Urgent"})

    def test_new_labels_default_to_not_notifying(self):
        from workspace.mail.models import MailAccount, MailLabel

        account = MailAccount.objects.create(
            owner=User.objects.create_user(username="defaultflag", password="pass"),
            email="d@example.test",
            imap_host="imap.example.test",
            smtp_host="smtp.example.test",
            username="d@example.test",
        )
        label = MailLabel.objects.create(account=account, name="Custom")
        self.assertFalse(label.notify_on_apply)


class NotifyOnApplyBackfillTests(TestCase):
    """The data migration flags pre-existing Urgent labels, case-insensitively."""

    def setUp(self):
        from workspace.mail.models import MailAccount, MailLabel

        self.account = MailAccount.objects.create(
            owner=User.objects.create_user(username="backfill", password="pass"),
            email="b@example.test",
            imap_host="imap.example.test",
            smtp_host="smtp.example.test",
            username="b@example.test",
        )
        # The post_save signal already seeded the defaults; reset so this test
        # exercises the migration rather than the signal.
        MailLabel.objects.filter(account=self.account).update(notify_on_apply=False)

    def test_backfill_flags_urgent_regardless_of_case(self):
        import importlib

        from django.apps import apps

        from workspace.mail.models import MailLabel

        MailLabel.objects.create(account=self.account, name="urgent bis")
        module = importlib.import_module(
            "workspace.mail.migrations.0031_seed_notify_on_apply"
        )

        class _Editor:
            class connection:
                alias = "default"

        module.flag_urgent_labels(apps, _Editor)

        self.assertTrue(
            MailLabel.objects.get(account=self.account, name="Urgent").notify_on_apply
        )
        self.assertFalse(
            MailLabel.objects.get(
                account=self.account, name="urgent bis"
            ).notify_on_apply
        )

    def test_reverse_leaves_every_flag_untouched(self):
        """Reversing must not write to notify_on_apply at all.

        The column is user-editable through the label API, so a reverse cannot
        tell a seeded default from a deliberate choice - including on "Urgent"
        itself, where a user who turned the flag off and then back on looks
        exactly like the migration's own write.
        """
        import importlib

        from django.apps import apps

        from workspace.mail.models import MailLabel

        module = importlib.import_module(
            "workspace.mail.migrations.0031_seed_notify_on_apply"
        )
        reverse = module.Migration.operations[0].reverse_code

        class _Editor:
            class connection:
                alias = "default"

        MailLabel.objects.filter(account=self.account, name="Urgent").update(
            notify_on_apply=False
        )
        MailLabel.objects.filter(account=self.account, name="Action").update(
            notify_on_apply=True
        )

        # Run whatever the migration actually declares, so this fails if the
        # reverse is ever replaced by something that writes.
        self.assertIs(reverse, migrations.RunPython.noop)
        reverse(apps, _Editor)

        self.assertFalse(
            MailLabel.objects.get(account=self.account, name="Urgent").notify_on_apply
        )
        self.assertTrue(
            MailLabel.objects.get(account=self.account, name="Action").notify_on_apply
        )
