"""Behaviour of PATCH /api/v1/mail/messages/<uuid> — the flag endpoint.

Written as the safety net for extracting the flag/count orchestration into
`services/triage.py`, so the shared service has to keep doing exactly what
the view did: apply locally whatever IMAP answers, and recompute the folder
(and label) counters in the same transaction.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.models import (
    MailAccount,
    MailFolder,
    MailLabel,
    MailMessage,
    MailMessageLabel,
)

User = get_user_model()


class MessageFlagsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="flagger", password="pass")
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
            unread_count=99,
        )
        self.message = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=1,
            subject="Invoice",
            is_read=False,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _url(self):
        return f"/api/v1/mail/messages/{self.message.uuid}"

    def test_marking_read_syncs_imap_and_recomputes_counts(self):
        with patch("workspace.mail.services.imap_messages.mark_read") as mark:
            resp = self.client.patch(self._url(), {"is_read": True}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.message.refresh_from_db()
        self.inbox.refresh_from_db()
        self.assertTrue(self.message.is_read)
        self.assertEqual(self.inbox.unread_count, 0)
        mark.assert_called_once_with(self.account, self.message)

    def test_marking_unread_syncs_imap(self):
        MailMessage.objects.filter(pk=self.message.pk).update(is_read=True)
        with patch("workspace.mail.services.imap_messages.mark_unread") as mark:
            resp = self.client.patch(self._url(), {"is_read": False}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.message.refresh_from_db()
        self.inbox.refresh_from_db()
        self.assertFalse(self.message.is_read)
        self.assertEqual(self.inbox.unread_count, 1)
        mark.assert_called_once()

    def test_starring_syncs_imap(self):
        with patch("workspace.mail.services.imap_messages.star_message") as star:
            resp = self.client.patch(self._url(), {"is_starred": True}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.message.refresh_from_db()
        self.assertTrue(self.message.is_starred)
        star.assert_called_once()

    def test_unstarring_syncs_imap(self):
        MailMessage.objects.filter(pk=self.message.pk).update(is_starred=True)
        with patch("workspace.mail.services.imap_messages.unstar_message") as unstar:
            resp = self.client.patch(self._url(), {"is_starred": False}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.message.refresh_from_db()
        self.assertFalse(self.message.is_starred)
        unstar.assert_called_once()

    def test_an_imap_failure_still_applies_the_flag_locally(self):
        # The mail UI is optimistic here on purpose: the next sync reconciles.
        with patch(
            "workspace.mail.services.imap_messages.star_message",
            side_effect=OSError("connection reset"),
        ):
            resp = self.client.patch(self._url(), {"is_starred": True}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.message.refresh_from_db()
        self.assertTrue(self.message.is_starred)

    def test_marking_read_recomputes_the_label_counters(self):
        label = MailLabel.objects.get(account=self.account, name="Urgent")
        MailMessageLabel.objects.create(message=self.message, label=label)
        MailLabel.objects.filter(pk=label.pk).update(unread_count=42)

        with patch("workspace.mail.services.imap_messages.mark_read"):
            self.client.patch(self._url(), {"is_read": True}, format="json")

        label.refresh_from_db()
        self.assertEqual(label.unread_count, 0)

    def test_ai_summary_is_stored_without_touching_imap(self):
        with patch("workspace.mail.services.imap_messages.mark_read") as mark:
            resp = self.client.patch(
                self._url(), {"ai_summary": "Pay before Friday."}, format="json"
            )

        self.assertEqual(resp.status_code, 200)
        self.message.refresh_from_db()
        self.assertEqual(self.message.ai_summary, "Pay before Friday.")
        mark.assert_not_called()
