"""Regression tests: in-app message deletion must settle its Notification.

Soft delete never CASCADEs the Notification.mail_message FK (that only fires
on a hard delete), and every mail queryset filters deleted_at, so a deleted
message can never again appear on a rendered page for mark_sources_read to
catch. A push that reached the user via a deep link (which opens the message
detail directly, without ever loading a folder list) can otherwise stay
unread forever once the message is deleted.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.notifications.models import Notification

User = get_user_model()


class DeleteClearsNotificationsMixin:
    def setUp(self):
        self.user = User.objects.create_user(username="delnotifuser", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.msg = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=42,
        )
        self.notif = Notification.objects.create(
            recipient=self.user,
            origin="mail",
            icon="",
            title="Hi",
            mail_message=self.msg,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)


class MessageDetailDeleteClearsNotificationTests(
    DeleteClearsNotificationsMixin, TestCase
):
    @patch("workspace.mail.services.imap_messages.delete_message")
    def test_deleting_the_message_marks_its_notification_read(self, _mock_delete):
        resp = self.client.delete(f"/api/v1/mail/messages/{self.msg.uuid}")
        self.assertEqual(resp.status_code, 204)
        self.notif.refresh_from_db()
        self.assertIsNotNone(self.notif.read_at)


class BatchDeleteClearsNotificationTests(DeleteClearsNotificationsMixin, TestCase):
    @patch("workspace.mail.services.imap_messages.delete_message")
    def test_batch_deleting_the_message_marks_its_notification_read(self, _mock_delete):
        resp = self.client.post(
            "/api/v1/mail/messages/batch-action",
            {"message_ids": [str(self.msg.uuid)], "action": "delete"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.notif.refresh_from_db()
        self.assertIsNotNone(self.notif.read_at)
