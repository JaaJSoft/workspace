"""Attaching the attachments of a stored message via ``attachment_uuids``.

A forward carries the original's attachments by uuid, on send and on draft
save alike. The uuids resolve through the user's own accounts only, and one
the user cannot reach is answered exactly like one that does not exist.
"""

import email
import email.policy
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.mail.models import MailAccount, MailAttachment, MailFolder, MailMessage
from workspace.mail.tests.smtp_recorder import RecordingSMTP

User = get_user_model()

PDF = b"%PDF-1.4 quarterly numbers"


def _account(owner, address):
    account = MailAccount.objects.create(
        owner=owner,
        email=address,
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username=address,
    )
    account.set_password("secret")
    account.save()
    return account


def _attachment(account, filename="report.pdf", data=PDF):
    folder = MailFolder.objects.create(
        account=account, name="INBOX", display_name="Inbox", folder_type="inbox"
    )
    message = MailMessage.objects.create(
        account=account, folder=folder, imap_uid=1, subject="Numbers"
    )
    return MailAttachment.objects.create(
        message=message,
        filename=filename,
        content_type="application/pdf",
        size=len(data),
        content=SimpleUploadedFile(filename, data, content_type="application/pdf"),
    )


def _parts_named(message, filename):
    return [part for part in message.walk() if part.get_filename() == filename]


class _ForwardSetup(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sender", password="p")
        self.other = User.objects.create_user(username="other", password="p")
        self.account = _account(self.user, "sender@example.com")
        self.client.force_authenticate(self.user)


@patch("workspace.mail.services.imap_sync.sync_folder_messages")
@patch("workspace.mail.services.imap_messages.append_to_sent")
@patch("workspace.mail.services.smtp.connect_smtp")
class SendForwardedAttachmentsTests(_ForwardSetup):
    def _send(self, attachment_uuids):
        return self.client.post(
            "/api/v1/mail/messages/send",
            {
                "account_id": str(self.account.uuid),
                "to": ["bob@example.com"],
                "subject": "Fwd: Numbers",
                "body_text": "see below",
                "attachment_uuids": [str(u) for u in attachment_uuids],
            },
            format="json",
        )

    def test_forwarded_attachment_reaches_the_outgoing_message(
        self, mock_connect, _append, _sync
    ):
        server = RecordingSMTP()
        mock_connect.return_value = server
        original = _attachment(self.account)

        resp = self._send([original.uuid])

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        (part,) = _parts_named(server.message, "report.pdf")
        self.assertEqual(part.get_payload(decode=True), PDF)
        self.assertEqual(part.get_content_type(), "application/pdf")

    def test_attachment_of_another_users_message_answers_like_an_unknown_one(
        self, mock_connect, _append, _sync
    ):
        server = RecordingSMTP()
        mock_connect.return_value = server
        foreign = _attachment(_account(self.other, "other@example.com"))

        hidden = self._send([foreign.uuid])
        unknown = self._send([uuid.uuid4()])

        self.assertEqual(hidden.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            (hidden.status_code, hidden.content),
            (unknown.status_code, unknown.content),
        )
        self.assertEqual(server.transactions, 0)

    def test_one_unreachable_uuid_refuses_the_whole_send(
        self, mock_connect, _append, _sync
    ):
        server = RecordingSMTP()
        mock_connect.return_value = server
        own = _attachment(self.account)
        foreign = _attachment(_account(self.other, "other@example.com"))

        resp = self._send([own.uuid, foreign.uuid])

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(server.transactions, 0)

    @override_settings(MAIL_MAX_ATTACHMENTS_SIZE=len(PDF) - 1)
    def test_forwarded_attachments_count_toward_the_size_limit(
        self, mock_connect, _append, _sync
    ):
        server = RecordingSMTP()
        mock_connect.return_value = server
        original = _attachment(self.account)

        resp = self._send([original.uuid])

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(server.transactions, 0)


@patch("workspace.mail.services.imap_messages.save_draft")
class DraftForwardedAttachmentsTests(_ForwardSetup):
    def _save(self, attachment_uuids):
        return self.client.post(
            "/api/v1/mail/drafts",
            {
                "account_id": str(self.account.uuid),
                "to": ["bob@example.com"],
                "subject": "Fwd: Numbers",
                "body_text": "see below",
                "attachment_uuids": [str(u) for u in attachment_uuids],
            },
            format="json",
        )

    def test_saved_draft_carries_the_forwarded_attachment(self, mock_save):
        mock_save.return_value = None
        original = _attachment(self.account)

        self._save([original.uuid])

        mock_save.assert_called_once()
        draft = email.message_from_bytes(
            mock_save.call_args[0][1], policy=email.policy.default
        )
        (part,) = _parts_named(draft, "report.pdf")
        self.assertEqual(part.get_payload(decode=True), PDF)

    def test_attachment_of_another_users_message_answers_like_an_unknown_one(
        self, mock_save
    ):
        foreign = _attachment(_account(self.other, "other@example.com"))

        hidden = self._save([foreign.uuid])
        unknown = self._save([uuid.uuid4()])

        self.assertEqual(hidden.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            (hidden.status_code, hidden.content),
            (unknown.status_code, unknown.content),
        )
        mock_save.assert_not_called()
