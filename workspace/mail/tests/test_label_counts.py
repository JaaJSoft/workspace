import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.mail.models import (
    MailAccount,
    MailFolder,
    MailLabel,
    MailMessage,
    MailMessageLabel,
)
from workspace.mail.services.label_counts import refresh_labels_for_messages

User = get_user_model()

PARAMETER_LIMIT = 50


class RefreshLabelsForMessagesTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="labelcounts", password="pass")
        self.account = MailAccount.objects.create(
            owner=user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name="INBOX", display_name="Inbox"
        )

    def _labelled_message(self, uid, label, is_read=False):
        message = MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=uid, is_read=is_read
        )
        MailMessageLabel.objects.create(message=message, label=label)
        return message

    def _stale_label(self, name):
        return MailLabel.objects.create(
            account=self.account, name=name, unread_count=99
        )

    def test_recounts_the_labels_of_the_given_messages(self):
        label = self._stale_label("Important")
        unread = self._labelled_message(1, label)
        self._labelled_message(2, label, is_read=True)

        refresh_labels_for_messages([unread.pk])

        label.refresh_from_db()
        self.assertEqual(label.unread_count, 1)

    def test_leaves_the_labels_of_other_messages_alone(self):
        label = self._stale_label("Important")
        other = self._stale_label("Later")
        message = self._labelled_message(1, label)
        self._labelled_message(2, other)

        refresh_labels_for_messages([message.pk])

        other.refresh_from_db()
        self.assertEqual(other.unread_count, 99)

    def test_no_messages_is_a_no_op(self):
        label = self._stale_label("Important")
        self._labelled_message(1, label)

        refresh_labels_for_messages([])

        label.refresh_from_db()
        self.assertEqual(label.unread_count, 99)

    def test_takes_more_messages_than_a_statement_binds(self):
        # A folder marked read from another client hands over every message
        # it holds; SQLite binds 32766 parameters at most, lowered here.
        label = self._stale_label("Important")
        message = self._labelled_message(1, label)
        message_ids = [uuid.uuid4() for _ in range(PARAMETER_LIMIT * 2)]
        message_ids.append(message.pk)

        with sqlite_parameter_limit(PARAMETER_LIMIT):
            refresh_labels_for_messages(message_ids)

        label.refresh_from_db()
        self.assertEqual(label.unread_count, 1)
