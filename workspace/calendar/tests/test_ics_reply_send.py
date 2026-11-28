import email
import email.policy
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event, EventMember
from workspace.calendar.tasks import send_ics_reply
from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.users.services.settings import set_setting

User = get_user_model()


class ReplyFixtureMixin:
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
        )
        self.calendar = Calendar.objects.create(
            name="Invitations",
            owner=self.user,
            mail_account=self.account,
        )
        self.mail_msg = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            message_id="<invite@example.com>",
            imap_uid=1,
            subject="Invite",
            date=datetime(2026, 3, 1, tzinfo=UTC),
        )
        self.event = Event.objects.create(
            calendar=self.calendar,
            title="Sprint Review",
            start=datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
            end=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
            owner=self.user,
            ical_uid="evt-123@example.com",
            external_organizer="alice@external.com",
            source_message=self.mail_msg,
        )
        EventMember.objects.create(event=self.event, user=self.user)


class EventRespondReplyTest(ReplyFixtureMixin, APITestCase):
    @patch("workspace.calendar.tasks.send_ics_reply.delay")
    def test_accept_sends_reply_email(self, mock_send):
        self.client.force_authenticate(self.user)
        url = f"/api/v1/events/{self.event.pk}/response"
        resp = self.client.put(url, {"status": "accepted"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        mock_send.assert_called_once_with(str(self.event.pk), self.user.id, "accepted")

    @patch("workspace.calendar.tasks.send_ics_reply.delay")
    def test_decline_sends_reply_email(self, mock_send):
        self.client.force_authenticate(self.user)
        url = f"/api/v1/events/{self.event.pk}/response"
        resp = self.client.put(url, {"status": "declined"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        mock_send.assert_called_once_with(str(self.event.pk), self.user.id, "declined")

    @patch("workspace.calendar.tasks.send_ics_reply.delay")
    def test_no_reply_for_non_email_event(self, mock_send):
        self.event.external_organizer = None
        self.event.source_message = None
        self.event.save()
        self.client.force_authenticate(self.user)
        url = f"/api/v1/events/{self.event.pk}/response"
        resp = self.client.put(url, {"status": "accepted"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        mock_send.assert_not_called()


class SendIcsReplyMessageTest(ReplyFixtureMixin, TestCase):
    """What the organizer receives when the member answers."""

    def tearDown(self):
        cache.clear()

    def _sent_message(self, response_status):
        server = MagicMock()
        with patch("workspace.mail.services.smtp.connect_smtp", return_value=server):
            send_ics_reply(str(self.event.pk), self.user.id, response_status)
        sender, recipients, raw = server.sendmail.call_args.args
        self.assertEqual(sender, "bob@test.com")
        self.assertEqual(recipients, ["alice@external.com"])
        server.quit.assert_called_once()
        if isinstance(raw, str):
            raw = raw.encode("ascii")
        return email.message_from_bytes(raw, policy=email.policy.default)

    def test_reply_is_written_in_the_members_language(self):
        set_setting(self.user, "core", "language", "fr")

        msg = self._sent_message("accepted")

        self.assertEqual(msg["Subject"], "Accepté\u00a0: Sprint Review")
        body = msg.get_body(preferencelist=("plain",))
        self.assertEqual(
            body.get_content().strip(), "bob a accepté «\u00a0Sprint Review\u00a0»."
        )

    def test_reply_carries_the_itip_part(self):
        msg = self._sent_message("declined")

        self.assertEqual(msg["Subject"], "Declined: Sprint Review")
        self.assertEqual(msg["To"], "alice@external.com")
        self.assertEqual(msg["From"].addresses[0].addr_spec, "bob@test.com")
        calendar_part = next(
            part for part in msg.walk() if part.get_content_type() == "text/calendar"
        )
        self.assertEqual(calendar_part.get_param("method"), "REPLY")
        ics = calendar_part.get_content()
        self.assertIn("METHOD:REPLY", ics)
        self.assertIn("PARTSTAT=DECLINED", ics)
