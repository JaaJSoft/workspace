from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.imap_sync import FETCH_BATCH_SIZE, sync_folder_messages
from workspace.mail.tests.imap_fake import FakeImapServer, fake_imap

User = get_user_model()

ICS_CONTENT = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
DTSTART:20260301T100000Z
DTEND:20260301T110000Z
SUMMARY:Team Meeting
ORGANIZER:mailto:organizer@example.com
ATTENDEE:mailto:bob@test.com
END:VEVENT
END:VCALENDAR"""


def _invitation(number=1):
    """A raw email carrying a text/calendar part."""
    msg = MIMEMultipart("mixed")
    msg["Subject"] = "New Invite"
    msg["From"] = "organizer@example.com"
    msg["To"] = "bob@test.com"
    msg["Message-ID"] = f"<invite-{number}@example.com>"
    msg["Date"] = "Sun, 01 Mar 2026 10:00:00 +0000"
    msg.attach(MIMEText("You have been invited to a meeting.", "plain"))
    msg.attach(MIMEText(ICS_CONTENT, "calendar", "utf-8"))
    return msg.as_bytes()


class SyncCalendarHookTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="bob", email="bob@test.com", password="pass123"
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="bob@test.com",
            imap_host="imap.test.com",
            imap_port=993,
            imap_use_ssl=True,
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_use_tls=True,
            username="bob@test.com",
        )
        self.account.set_password("pass")
        self.account.save()
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
            uid_validity=1,
            last_sync_uid=0,
        )
        self.server = FakeImapServer()
        self.server.mailbox("INBOX").uidvalidity = self.folder.uid_validity

    @patch("workspace.calendar.services.ics_processor.process_calendar_emails")
    def test_sync_skips_ics_processor_when_no_new_messages(self, mock_process):
        """Pre-existing ICS messages are NOT re-processed on every sync.

        Calendar reprocessing must be scoped to messages actually parsed in
        the current sync pass, otherwise every sync pays O(all_cal_messages).
        """
        uid = self.server.deliver("INBOX", raw=_invitation())
        MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            message_id="<old@example.com>",
            imap_uid=uid,
            subject="Old Invite",
            date="2026-03-01T10:00:00Z",
            has_calendar_event=True,
        )

        with fake_imap(self.server):
            sync_folder_messages(self.account, self.folder)

        # No new UIDs parsed in this pass → no calendar reprocessing.
        mock_process.assert_not_called()

    @patch("workspace.ai.tasks.mail.classify_mail_messages.delay")
    @patch("workspace.ai.client.is_ai_enabled", return_value=False)
    @patch("workspace.calendar.services.ics_processor.process_calendar_emails")
    def test_sync_calls_ics_processor_for_newly_parsed_messages(
        self,
        mock_process,
        _mock_ai_enabled,
        _mock_classify_delay,
    ):
        """Newly parsed ICS messages are handed to process_calendar_emails."""
        uid = self.server.deliver("INBOX", raw=_invitation())

        with fake_imap(self.server):
            sync_folder_messages(self.account, self.folder)

        new_msg = MailMessage.objects.get(folder=self.folder, imap_uid=uid)
        mock_process.assert_called_once()
        processed = list(mock_process.call_args[0][0])
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0].pk, new_msg.pk)

    @patch("workspace.ai.tasks.mail.classify_mail_messages.delay")
    @patch("workspace.ai.client.is_ai_enabled", return_value=False)
    @patch("workspace.calendar.services.ics_processor.process_calendar_emails")
    def test_sync_hands_over_more_new_messages_than_a_statement_binds(
        self,
        mock_process,
        _mock_ai_enabled,
        _mock_classify_delay,
    ):
        # Above a fetch batch: its existence probe binds one UID each.
        limit = FETCH_BATCH_SIZE + 10
        count = limit + 10
        for number in range(count):
            self.server.deliver("INBOX", raw=_invitation(number))

        with fake_imap(self.server), sqlite_parameter_limit(limit):
            sync_folder_messages(self.account, self.folder)

        mock_process.assert_called_once()
        self.assertEqual(mock_process.call_args[0][0].count(), count)

    @patch("workspace.ai.client.is_ai_enabled", return_value=False)
    @patch("workspace.calendar.services.ics_processor.process_calendar_emails")
    def test_sync_imports_no_invitation_from_folders_that_receive_no_new_mail(
        self, mock_process, _mock_ai_enabled
    ):
        # Spam would put calendar spam in the calendar; Sent holds the copy of
        # an invitation the user sent from another client.
        for folder_type in ("sent", "drafts", "spam", "trash"):
            with self.subTest(folder_type=folder_type):
                folder = MailFolder.objects.create(
                    account=self.account,
                    name=folder_type,
                    display_name=folder_type,
                    folder_type=folder_type,
                )
                self.server.deliver(folder_type, raw=_invitation())

                with fake_imap(self.server):
                    sync_folder_messages(self.account, folder)

                self.assertTrue(
                    MailMessage.objects.filter(
                        folder=folder, has_calendar_event=True
                    ).exists()
                )
                mock_process.assert_not_called()
