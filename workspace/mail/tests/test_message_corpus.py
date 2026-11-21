"""What import makes of the messages in ``corpus/``.

Each file there is a whole message as a mail program writes it: the nesting
Apple Mail, Outlook and Gmail give their parts, the charsets and header
encodings that predate UTF-8, the file names encoded three different ways.
They are written by hand after those programs' output, with invented people
and addresses.

These tests pin the stored result, not the calls that produce it, so they
hold whatever parses the message. A case marked as an expected failure is a
known bug, named in its reason: fixing it turns the mark into an error.
"""

import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder
from workspace.mail.services.imap_parse import _parse_message

CORPUS = Path(__file__).parent / "corpus"

User = get_user_model()


def known_bug(reason):
    """Mark a test that fails until the bug `reason` names is fixed."""
    return unittest.expectedFailure


def _lines(text):
    """`text` with the line endings of the wire folded to ``\\n``."""
    return text.replace("\r\n", "\n")


def _words(text):
    return " ".join(text.split())


class MessageCorpusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="corpus", password="pass")
        cls.account = MailAccount.objects.create(
            owner=cls.user,
            email="pierre@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="pierre@example.com",
        )
        cls.folder = MailFolder.objects.create(
            account=cls.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )

    def parse(self, name):
        raw = (CORPUS / name).read_bytes()
        return _parse_message(raw, self.account, self.folder, 1, "")

    def attachments(self, message):
        return [
            (a.filename, a.content_type, a.content_id, a.is_inline)
            for a in message.attachments.order_by("created_at", "uuid")
        ]

    # -- Apple Mail ---------------------------------------------------------

    def test_apple_mail_message(self):
        message = self.parse("apple_mail_inline_pdf.eml")

        self.assertEqual(message.subject, "Devis révisé pour la salle")
        self.assertEqual(
            (message.from_name, message.from_email),
            ("Amélie Durand", "amelie@example.org"),
        )
        self.assertEqual(
            message.to_addresses,
            [{"name": "Pierre Martin", "email": "pierre@example.com"}],
        )
        self.assertEqual(
            message.message_id, "<5D0E1A52-2C8B-4B0F-9B55-6F1D8A3C7E21@example.org>"
        )
        self.assertEqual(
            message.date,
            datetime(2026, 11, 3, 8, 41, 17, tzinfo=timezone(timedelta(hours=1))),
        )
        self.assertEqual(
            _lines(message.body_text),
            "Bonjour Pierre,\n\nVoici le devis révisé.\n\n\n\nBien à toi,\nAmélie",
        )
        self.assertEqual(
            _words(message.snippet),
            "Bonjour Pierre, Voici le devis révisé. Bien à toi, Amélie",
        )
        self.assertIn("<div>Voici le devis révisé.</div>", message.body_html)

    @known_bug("#1749: an inline file without a Content-ID is dropped.")
    def test_apple_mail_inline_file_is_an_attachment(self):
        message = self.parse("apple_mail_inline_pdf.eml")

        self.assertEqual(
            self.attachments(message),
            [("Devis salle.pdf", "application/pdf", "", False)],
        )
        self.assertTrue(message.has_attachments)

    # -- Outlook ------------------------------------------------------------

    def test_outlook_reply(self):
        message = self.parse("outlook_signature_logo.eml")

        self.assertEqual(
            message.subject, "RE: Compte rendu de la réunion du 3 novembre"
        )
        self.assertEqual(
            (message.from_name, message.from_email),
            ("Dupont, Jean", "jean.dupont@contoso.example"),
        )
        self.assertEqual(
            message.cc_addresses,
            [
                {"name": "Renée Lefèvre", "email": "renee@contoso.example"},
                {"name": "Support (Contoso)", "email": "support@contoso.example"},
            ],
        )
        self.assertEqual(
            message.message_id,
            "<PA4PR83MB0526D7C1A0E94F5C8B3D2E1A9C0A2"
            "@PA4PR83MB0526.EURPRD83.prod.outlook.com>",
        )
        self.assertEqual(message.in_reply_to, "<second@example.com>")
        self.assertEqual(message.references, "<first@example.com> <second@example.com>")
        self.assertTrue(
            _lines(message.body_text).startswith(
                "Bonjour,\n\nMerci pour le compte rendu, c'est noté.\n\nJean Dupont"
            )
        )
        self.assertIn(
            '<p class="MsoNormal">Merci pour le compte rendu, c\'est noté.</p>',
            message.body_html,
        )
        self.assertNotIn("<style", message.body_html)
        self.assertEqual(
            self.attachments(message),
            [("image001.png", "image/png", "image001.png@01DC4DB6.5C1A2B30", True)],
        )

    def test_outlook_attachment(self):
        message = self.parse("outlook_attachment.eml")

        self.assertEqual(
            message.to_addresses,
            [{"name": "pierre@example.com", "email": "pierre@example.com"}],
        )
        self.assertEqual(
            _lines(message.body_text),
            "Hello,\n\nPlease find the budget attached.\n\nJean\n",
        )
        self.assertEqual(
            message.body_html.strip(),
            "<p>Hello,</p><p>Please find the budget attached.</p><p>Jean</p>",
        )
        self.assertEqual(
            self.attachments(message),
            [
                (
                    "Budget prévisionnel 2027.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "",
                    False,
                )
            ],
        )
        self.assertTrue(message.has_attachments)
        self.assertFalse(message.has_calendar_event)

    # -- Gmail --------------------------------------------------------------

    def test_gmail_pasted_screenshot(self):
        message = self.parse("gmail_pasted_screenshot.eml")

        self.assertEqual(
            message.message_id,
            "<CAK7dXyQ4fT1n8sWk3uR2mZb0hV6pJ9cL5aE+oD=gN1xY7wq@mail.gmail.com>",
        )
        self.assertEqual(
            _lines(message.body_text),
            "Voilà ce que je vois :\n\n[image: image.png]\n\nLucie",
        )
        self.assertTrue(
            message.body_html.startswith('<div dir="ltr">Voilà ce que je vois :')
        )
        self.assertEqual(
            self.attachments(message),
            [("image.png", "image/png", "ii_m3a9x7k20", True)],
        )

    @known_bug("#1750: the sanitizer drops cid: sources.")
    def test_embedded_pictures_keep_their_place_in_the_body(self):
        for name, reference in (
            ("gmail_pasted_screenshot.eml", "cid:ii_m3a9x7k20"),
            ("outlook_signature_logo.eml", "cid:image001.png@01DC4DB6.5C1A2B30"),
        ):
            with self.subTest(name):
                self.assertIn(reference, self.parse(name).body_html)

    @known_bug("#1749: a picture the body displays counts as an attachment.")
    def test_embedded_pictures_are_not_attachments(self):
        for name in ("gmail_pasted_screenshot.eml", "outlook_signature_logo.eml"):
            with self.subTest(name):
                self.assertFalse(self.parse(name).has_attachments)

    # -- Bodies -------------------------------------------------------------

    def test_html_only_newsletter(self):
        message = self.parse("html_only_newsletter.eml")

        self.assertEqual(message.subject, "La lettre de novembre 🍂")
        self.assertEqual(
            (message.from_name, message.from_email),
            ("La Lettre", "newsletter@news.example.net"),
        )
        self.assertEqual(message.date, datetime(2026, 11, 6, 6, 0, 2, tzinfo=UTC))
        self.assertIn("<h1>La lettre de novembre</h1>", message.body_html)
        self.assertIn(
            "<p>Trois nouveautés à découvrir ce mois-ci dans votre espace.</p>",
            message.body_html,
        )
        self.assertIn(
            'href="https://news.example.net/novembre?utm_source=mail"',
            message.body_html,
        )
        self.assertNotIn("script", message.body_html)
        self.assertNotIn("track(", message.body_html)
        self.assertEqual(self.attachments(message), [])
        self.assertFalse(message.has_attachments)

    @known_bug("#1750: the text is only ever read from a text/plain part.")
    def test_html_only_message_has_text_to_show_and_search(self):
        message = self.parse("html_only_newsletter.eml")

        self.assertIn("Trois nouveautés à découvrir", message.body_text)
        self.assertIn("Trois nouveautés à découvrir", message.snippet)

    def test_bare_minimum_message(self):
        before = datetime.now(UTC)
        message = self.parse("bare_minimum.eml")

        self.assertEqual(message.subject, "Cron <root@server> /usr/local/bin/backup")
        self.assertEqual(
            (message.from_name, message.from_email), ("", "cron@server.example.net")
        )
        self.assertEqual(message.to_addresses, [{"name": "", "email": "root"}])
        self.assertEqual(message.message_id, "")
        self.assertGreaterEqual(message.date, before, "no Date header: import time")
        self.assertEqual(_lines(message.body_text), "backup: 3 volumes saved\n")
        self.assertEqual(message.body_html, "")

    # -- Charsets -----------------------------------------------------------

    def test_iso_2022_jp(self):
        message = self.parse("iso_2022_jp.eml")

        self.assertEqual(message.subject, "会議のお知らせ")
        self.assertEqual(
            (message.from_name, message.from_email), ("佐藤 花子", "sato@example.jp")
        )
        self.assertEqual(
            message.to_addresses, [{"name": "田中 太郎", "email": "tanaka@example.jp"}]
        )
        self.assertEqual(
            _lines(message.body_text),
            "田中様\n\n来週の会議は火曜日の午後3時からです。\n\n佐藤\n",
        )
        self.assertEqual(
            message.date,
            datetime(2026, 11, 9, 10, 5, tzinfo=timezone(timedelta(hours=9))),
        )

    def test_koi8_r_sent_as_raw_bytes(self):
        message = self.parse("koi8_r_8bit.eml")

        self.assertEqual(message.subject, "Привет из Москвы")
        self.assertEqual(message.from_name, "Иван Петров")
        self.assertEqual(_lines(message.body_text), "Здравствуйте!\n\nКак дела?\n")

    def test_windows_1252_punctuation(self):
        message = self.parse("windows_1252_quotes.eml")

        self.assertEqual(message.subject, "L’offre à 49 € – derniers jours")
        self.assertEqual(
            _lines(message.body_text),
            "L’offre « spéciale » à 49 € se termine bientôt… Œuvre de l’équipe.\n",
        )

    def test_hebrew_labelled_iso_8859_8_i(self):
        message = self.parse("hebrew_iso_8859_8_i.eml")

        self.assertEqual(message.subject, "שלום")
        self.assertEqual(message.body_text, "שלום עולם")

    @known_bug("#1746: unknown-8bit raises LookupError and the message is skipped.")
    def test_a_charset_python_does_not_know_still_imports(self):
        message = self.parse("unknown_8bit_charset.eml")

        self.assertEqual(message.from_email, "cafe@example.net")
        self.assertTrue(message.subject.startswith("R"))
        self.assertTrue(message.subject.endswith("servation confirm�e"))
        self.assertTrue(message.body_text.startswith("Votre table est r"))
        self.assertIn("pour 20 h.", message.body_text)

    @known_bug("#1746: raw 8-bit headers decode as unknown-8bit, which raises.")
    def test_headers_written_in_raw_utf8_still_import(self):
        message = self.parse("raw_utf8_headers.eml")

        self.assertEqual(message.subject, "Réunion décalée à 15 h")
        self.assertEqual(
            (message.from_name, message.from_email), ("Zoé Müller", "zoe@example.net")
        )
        self.assertEqual(_lines(message.body_text), "À tout à l'heure.\n")

    # -- Headers ------------------------------------------------------------

    def test_header_edge_cases(self):
        message = self.parse("header_edge_cases.eml")

        self.assertEqual(
            message.subject,
            "A subject folded over two lines, with un mot encodé collé",
        )
        self.assertEqual(
            (message.from_name, message.from_email),
            ("Martin, Zoe (Achats)", "zoe.martin@example.net"),
        )
        self.assertEqual(
            message.to_addresses,
            [
                {"name": "O'Neil, Sean", "email": "sean@example.com"},
                {"name": "", "email": "pierre@example.com"},
                {"name": "Jürgen Müller", "email": "juergen@example.de"},
                {"name": "", "email": "anna@example.com"},
                {"name": "", "email": "bob@example.com"},
            ],
        )
        self.assertEqual(message.cc_addresses, [])
        self.assertEqual(
            message.bcc_addresses, [{"name": "", "email": "secret@example.com"}]
        )
        self.assertEqual(message.reply_to, "Achats <achats@example.net>")
        self.assertEqual(message.in_reply_to, "<parent@example.com>")
        self.assertEqual(
            message.references,
            "<root@example.com> <middle@example.com> <parent@example.com>",
        )
        self.assertEqual(message.date, datetime(2026, 11, 18, 8, 0, tzinfo=UTC))
        self.assertEqual(_lines(message.body_text), "First line.\nSecond line.\n")

    # -- Attachments --------------------------------------------------------

    def test_file_names_in_every_encoding(self):
        message = self.parse("attachment_filenames.eml")

        self.assertEqual(
            self.attachments(message),
            [
                ("réunion du 3 novembre.pdf", "application/pdf", "", False),
                ("données.csv", "text/csv", "", False),
                (
                    "un nom de fichier vraiment très long qui dépasse une ligne.pdf",
                    "application/pdf",
                    "",
                    False,
                ),
                ("attachment.octet-stream", "application/octet-stream", "", False),
            ],
        )
        sizes = [a.size for a in message.attachments.order_by("created_at", "uuid")]
        self.assertEqual(sizes, [77, 19, 77, 0])
        csv = message.attachments.get(filename="données.csv")
        with csv.content.open("rb") as content:
            self.assertEqual(content.read().decode(), "nom;âge\r\nZoé;31\r\n")
        self.assertEqual(_lines(message.body_text), "Trois fichiers joints.\n")

    def test_calendar_invitation(self):
        message = self.parse("calendar_invitation.eml")

        self.assertEqual(message.subject, "Invitation : Point d'équipe")
        self.assertTrue(message.has_calendar_event)
        self.assertTrue(message.has_attachments)
        self.assertEqual(
            self.attachments(message),
            [
                ("invite.ics", "text/calendar", "", False),
                ("invite.ics", "application/ics", "", False),
            ],
        )
        invite = message.attachments.get(content_type="text/calendar")
        with invite.content.open("rb") as content:
            ics = content.read().decode()
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertIn("SUMMARY:Point d'équipe\r\n", ics)
        self.assertEqual(
            message.body_text,
            "Vous êtes invité : Point d'équipe, vendredi 20 novembre à 15 h.",
        )
        self.assertEqual(
            message.body_html,
            "<p>Vous êtes invité : <b>Point d'équipe</b>, "
            "vendredi 20 novembre à 15 h.</p>",
        )

    def test_forwarded_message(self):
        message = self.parse("forwarded_as_attachment.eml")

        self.assertEqual(message.subject, "Fwd: Facture 2026-114")
        self.assertEqual(message.from_email, "lucie.bernard@gmail.example")
        self.assertEqual(
            _lines(message.body_text),
            "Pour info, la facture est dans le message joint.\n",
        )
        self.assertTrue(message.has_attachments)

    @known_bug("#1749: an attached message is dropped, and its own files hoisted.")
    def test_forwarded_message_is_kept_as_a_file(self):
        message = self.parse("forwarded_as_attachment.eml")

        self.assertEqual(
            self.attachments(message),
            [("Facture 2026-114.eml", "message/rfc822", "", False)],
        )
