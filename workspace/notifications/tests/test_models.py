from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from workspace.chat.models import Conversation
from workspace.files.models import File
from workspace.notifications.models import SOURCE_FIELD_NAMES, Notification
from workspace.notifications.services.notifications import SOURCE_FIELDS

User = get_user_model()


class NotificationSourceFieldsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")

    def test_source_fks_default_to_null(self):
        notif = Notification.objects.create(
            recipient=self.alice, origin="mail", icon="", title="Sourceless"
        )
        self.assertIsNone(notif.conversation_id)
        self.assertIsNone(notif.file_id)
        self.assertIsNone(notif.task_id)
        self.assertIsNone(notif.event_id)
        self.assertIsNone(notif.poll_id)

    def test_single_source_fk_accepted(self):
        conv = Conversation.objects.create(created_by=self.alice, kind="dm")
        notif = Notification.objects.create(
            recipient=self.alice,
            origin="chat",
            icon="",
            title="Hi",
            conversation=conv,
        )
        self.assertEqual(notif.conversation_id, conv.pk)

    def test_two_source_fks_rejected(self):
        conv = Conversation.objects.create(created_by=self.alice, kind="dm")
        file_obj = File.objects.create(owner=self.alice, name="a.txt", node_type="file")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Notification.objects.create(
                recipient=self.alice,
                origin="chat",
                icon="",
                title="Bad",
                conversation=conv,
                file=file_obj,
            )

    def test_deleting_source_cascades(self):
        conv = Conversation.objects.create(created_by=self.alice, kind="dm")
        Notification.objects.create(
            recipient=self.alice,
            origin="chat",
            icon="",
            title="Hi",
            conversation=conv,
        )
        conv.delete()
        self.assertEqual(Notification.objects.count(), 0)


class NotificationSourceRegistryTests(TestCase):
    """The source field list is declared once and consumed everywhere."""

    def test_every_source_name_is_a_real_fk_on_notification(self):
        for name in SOURCE_FIELD_NAMES:
            field = Notification._meta.get_field(name)
            self.assertTrue(
                field.is_relation and field.many_to_one,
                f"{name} is declared as a notification source but is not a FK",
            )

    def test_source_fields_map_covers_every_source_fk(self):
        # SOURCE_FIELDS cannot be derived (a model label is not recoverable
        # from a field name), so it is the one list that can still drift.
        self.assertEqual(set(SOURCE_FIELDS.values()), set(SOURCE_FIELD_NAMES))

    def test_cooldown_attrs_are_derived_from_the_source_names(self):
        from workspace.notifications.tasks import _SOURCE_ID_ATTRS

        self.assertEqual(
            tuple(_SOURCE_ID_ATTRS),
            tuple(f"{name}_id" for name in SOURCE_FIELD_NAMES),
        )


class NotificationMailSourceTests(TestCase):
    def setUp(self):
        from workspace.mail.models import MailAccount, MailFolder, MailMessage

        self.alice = User.objects.create_user(username="mailsrc", password="pass")
        account = MailAccount.objects.create(
            owner=self.alice,
            email="a@example.test",
            imap_host="imap.example.test",
            smtp_host="smtp.example.test",
            username="a@example.test",
        )
        folder = MailFolder.objects.create(
            account=account, name="INBOX", display_name="Inbox", folder_type="inbox"
        )
        self.message = MailMessage.objects.create(
            account=account, folder=folder, imap_uid=1, subject="Hello"
        )

    def test_mail_message_accepted_as_a_source(self):
        notif = Notification.objects.create(
            recipient=self.alice,
            origin="mail",
            icon="",
            title="Hello",
            mail_message=self.message,
        )
        self.assertEqual(notif.mail_message_id, self.message.pk)

    def test_mail_message_with_another_source_rejected(self):
        conv = Conversation.objects.create(created_by=self.alice, kind="dm")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Notification.objects.create(
                recipient=self.alice,
                origin="mail",
                icon="",
                title="Bad",
                mail_message=self.message,
                conversation=conv,
            )

    def test_deleting_the_message_cascades(self):
        Notification.objects.create(
            recipient=self.alice,
            origin="mail",
            icon="",
            title="Hello",
            mail_message=self.message,
        )
        self.message.delete()
        self.assertEqual(Notification.objects.count(), 0)

    def test_source_field_resolves_for_a_mail_message(self):
        from workspace.notifications.services.notifications import source_field

        self.assertEqual(source_field(self.message), "mail_message")
