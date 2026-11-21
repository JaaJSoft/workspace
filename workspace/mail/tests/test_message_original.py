"""Download .eml and Show headers: the message as the server stores it."""

import uuid

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.tests.imap_fake import fake_imap, raw_message
from workspace.mail.views.messages import eml_filename

User = get_user_model()

RAW = raw_message(
    subject="Quarterly report",
    message_id="<report@example.com>",
    body="See attached.",
    Received="from mx.example.com by imap.example.com",
)


class OriginalMessageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="owner@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="owner@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.server = self.enterContext(fake_imap())
        self.server.deliver("INBOX", raw=RAW, uid=42)
        self.msg = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=42,
            subject="Quarterly report",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def original(self, message_uuid=None):
        return self.client.get(
            f"/api/v1/mail/messages/{message_uuid or self.msg.uuid}/original"
        )

    def headers(self, message_uuid=None):
        return self.client.get(
            f"/api/v1/mail/messages/{message_uuid or self.msg.uuid}/headers"
        )

    def test_downloads_every_byte_the_server_holds(self):
        resp = self.original()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, RAW)
        self.assertEqual(resp["Content-Type"], "message/rfc822")
        self.assertEqual(
            resp["Content-Disposition"], 'attachment; filename="Quarterly report.eml"'
        )
        self.assertEqual(resp["Cache-Control"], "private, no-store")
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")

    def test_shows_the_header_block_alone(self):
        resp = self.headers()

        self.assertEqual(resp.status_code, 200)
        headers = resp.json()["headers"]
        self.assertIn("Received: from mx.example.com by imap.example.com", headers)
        self.assertIn("Message-ID: <report@example.com>", headers)
        self.assertNotIn("See attached.", headers)

    def test_reading_it_leaves_the_message_unread(self):
        self.original()
        self.headers()

        self.assertEqual(self.server.flags("INBOX", 42), set())
        selects = self.server.named("select")
        self.assertTrue(all(readonly for _name, _box, readonly in selects))

    def test_another_users_message_answers_like_one_that_does_not_exist(self):
        stranger = User.objects.create_user(username="stranger", password="pass")
        self.client.force_authenticate(user=stranger)

        for endpoint in (self.original, self.headers):
            hidden = endpoint()
            absent = endpoint(uuid.uuid4())
            self.assertEqual(hidden.status_code, 404)
            self.assertEqual(
                (hidden.status_code, hidden.content),
                (absent.status_code, absent.content),
            )
        self.assertEqual(self.server.named("select"), [])

    def test_a_deleted_message_is_not_found(self):
        self.msg.deleted_at = self.msg.created_at
        self.msg.save(update_fields=["deleted_at"])

        self.assertEqual(self.original().status_code, 404)

    def test_a_message_gone_from_the_server_is_not_found(self):
        self.server.remove("INBOX", 42)

        self.assertEqual(self.original().status_code, 404)
        self.assertEqual(self.headers().status_code, 404)

    def test_a_server_that_refuses_answers_bad_gateway(self):
        self.server.fail("fetch")

        self.assertEqual(self.original().status_code, 502)
        self.assertEqual(self.headers().status_code, 502)


class EmlFilenameTests(SimpleTestCase):
    def test_keeps_a_plain_subject(self):
        self.assertEqual(eml_filename("Réunion lundi"), "Réunion lundi.eml")

    def test_drops_what_a_file_name_cannot_hold(self):
        self.assertEqual(eml_filename('Re: a/b\\c "d"?\r\n'), "Re a b c d.eml")

    def test_falls_back_on_an_empty_subject(self):
        self.assertEqual(eml_filename(""), "message.eml")
        self.assertEqual(eml_filename(" ... "), "message.eml")

    def test_caps_a_long_subject(self):
        self.assertEqual(len(eml_filename("x" * 500)), 104)
