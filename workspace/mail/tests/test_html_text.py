"""A message sent as HTML only still gets a text body and a preview."""

import importlib
from email.mime.text import MIMEText
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.search import fts_messages
from workspace.mail.services.html_text import html_to_text
from workspace.mail.services.imap_parse import _parse_message

User = get_user_model()

NEWSLETTER = (
    "<p>Hello <b>Bob</b>,</p>"
    "<p>Your   quarterly\n report is ready:</p>"
    "<ul><li>Revenue</li><li>Churn</li></ul>"
    "<table><tr><td>Q1</td><td>12</td></tr><tr><td>Q2</td><td>15</td></tr></table>"
    "<div>Line<br>break</div>"
    '<p>Read it <a href="https://example.com">online</a> &amp; reply.</p>'
)


class HtmlToTextTests(SimpleTestCase):
    def test_blocks_become_lines_and_paragraphs(self):
        self.assertEqual(
            html_to_text(NEWSLETTER),
            "Hello Bob,\n\n"
            "Your quarterly report is ready:\n\n"
            "- Revenue\n- Churn\n"
            "Q1 12\nQ2 15\n"
            "Line\nbreak\n\n"
            "Read it online & reply.",
        )

    def test_preformatted_text_keeps_its_spacing(self):
        self.assertEqual(html_to_text("<pre>  a\n    b</pre>"), "a\n    b")

    def test_consecutive_breaks_are_kept(self):
        self.assertEqual(html_to_text("one<br><br>two"), "one\n\ntwo")

    def test_nothing_readable_gives_nothing(self):
        self.assertEqual(html_to_text(""), "")
        self.assertEqual(html_to_text("   "), "")
        self.assertEqual(html_to_text('<img src="x">'), "")

    def test_deep_nesting_does_not_overflow(self):
        html_to_text("<div>" * 5000 + "deep" + "</div>" * 5000)


class MailFixtures(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="htmlonly", password="pass")
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


class HtmlOnlyImportTests(MailFixtures):
    def _parse(self, part):
        part["Subject"] = "Report"
        part["From"] = "sender@example.com"
        part["Message-ID"] = "<report-001@example.com>"
        return _parse_message(part.as_bytes(), self.account, self.inbox, 1, ())

    def test_an_html_only_message_gets_a_text_body_and_a_preview(self):
        message = self._parse(MIMEText(NEWSLETTER, "html"))

        self.assertTrue(message.body_text.startswith("Hello Bob,\n\nYour quarterly"))
        self.assertTrue(message.snippet.startswith("Hello Bob,  Your quarterly report"))

    def test_an_html_only_message_is_found_by_the_words_of_its_body(self):
        self._parse(MIMEText(NEWSLETTER, "html"))

        found = fts_messages(MailMessage.objects.all(), "churn")

        self.assertEqual(found.count(), 1)

    def test_a_text_part_is_kept_as_it_is(self):
        message = self._parse(MIMEText("Plain words", "plain"))

        self.assertEqual(message.body_text, "Plain words")


class BackfillTests(MailFixtures):
    """Migration 0038 fills in the HTML-only messages stored before."""

    def _backfill(self):
        migration = importlib.import_module(
            "workspace.mail.migrations.0038_backfill_html_only_body_text"
        )
        migration.backfill(apps, SimpleNamespace(connection=connection))

    def _message(self, uid, **fields):
        return MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=uid,
            date=timezone.now(),
            **fields,
        )

    def test_an_html_only_message_gets_its_text_preview_and_search(self):
        message = self._message(1, body_html=NEWSLETTER)

        self._backfill()

        message.refresh_from_db()
        self.assertEqual(message.body_text, html_to_text(NEWSLETTER))
        self.assertTrue(message.snippet.startswith("Hello Bob,"))
        self.assertEqual(fts_messages(MailMessage.objects.all(), "churn").count(), 1)

    def test_a_message_with_text_or_a_preview_keeps_them(self):
        texted = self._message(1, body_html=NEWSLETTER, body_text="Original")
        previewed = self._message(2, body_html=NEWSLETTER, snippet="Kept")

        self._backfill()

        texted.refresh_from_db()
        previewed.refresh_from_db()
        self.assertEqual(texted.body_text, "Original")
        self.assertEqual(previewed.snippet, "Kept")
        self.assertEqual(previewed.body_text, html_to_text(NEWSLETTER))
