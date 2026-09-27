"""The streamed send against the real smtplib and imaplib clients.

Streaming bypasses `SMTP.sendmail` and `IMAP4.append`, so it leans on the
lower-level halves of both clients (`mail`/`rcpt`/`docmd`/`send`, and the
`literal` imaplib sends after a continuation). Minimal servers on loopback
check that a Python upgrade has not changed what those halves put on the
wire.
"""

import imaplib
import io
import re
import smtplib
import socketserver
import threading
from email import message_from_bytes
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder
from workspace.mail.services.sending import deliver_email

User = get_user_model()

PAYLOAD = bytes(range(256)) * 4000


class _SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        self.wfile.write(b"220 localhost ready\r\n")
        while line := self.rfile.readline():
            command = line[:4].upper()
            if command == b"EHLO":
                self.wfile.write(b"250-localhost\r\n250 SIZE 100000000\r\n")
            elif command == b"MAIL":
                server.mail_from = line
                self.wfile.write(b"250 OK\r\n")
            elif command == b"RCPT":
                server.recipients.append(line)
                self.wfile.write(b"250 OK\r\n")
            elif command == b"DATA":
                self.wfile.write(b"354 Go ahead\r\n")
                data = b""
                while (chunk := self.rfile.readline()) != b".\r\n":
                    data += chunk[1:] if chunk.startswith(b"..") else chunk
                server.data = data
                self.wfile.write(b"250 Queued\r\n")
            elif command == b"QUIT":
                self.wfile.write(b"221 Bye\r\n")
                return


class _IMAPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        self.wfile.write(b"* PREAUTH ready\r\n")
        while line := self.rfile.readline():
            tag, command = line.split(b" ", 2)[:2]
            command = command.strip().upper()
            if command == b"CAPABILITY":
                self.wfile.write(b"* CAPABILITY IMAP4rev1\r\n" + tag + b" OK done\r\n")
            elif command in (b"SELECT", b"EXAMINE"):
                self.wfile.write(b"* 0 EXISTS\r\n" + tag + b" OK done\r\n")
            elif command == b"UID":
                self.wfile.write(b"* SEARCH\r\n" + tag + b" OK done\r\n")
            elif command == b"APPEND":
                server.append_command = line
                size = int(re.search(rb"\{(\d+)\}\r\n$", line).group(1))
                self.wfile.write(b"+ Ready\r\n")
                server.literal = self.rfile.read(size)
                self.rfile.readline()
                self.wfile.write(tag + b" OK APPEND completed\r\n")
            elif command == b"LOGOUT":
                self.wfile.write(b"* BYE\r\n" + tag + b" OK done\r\n")
                return


def _serve(handler, test):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    server.recipients = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server


class SendOverTheWireTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="wire", password="pw")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="me@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="me@example.com",
        )
        MailFolder.objects.create(
            account=self.account, name="Sent", display_name="Sent", folder_type="sent"
        )

    def test_real_clients_deliver_and_archive_the_streamed_message(self):
        smtp_server = _serve(_SMTPHandler, self)
        imap_server = _serve(_IMAPHandler, self)
        attachment = io.BytesIO(PAYLOAD)
        attachment.name = "data.bin"

        def connect_smtp(account):
            client = smtplib.SMTP(*smtp_server.server_address, timeout=10)
            client.ehlo()
            return client

        with (
            patch("workspace.mail.services.smtp.connect_smtp", connect_smtp),
            patch(
                "workspace.mail.services.imap_messages.connect_imap",
                lambda account: imaplib.IMAP4(*imap_server.server_address, timeout=10),
            ),
            patch("workspace.mail.services.imap_sync.sync_folder_messages"),
        ):
            delivery = deliver_email(
                self.account,
                to=["bob@example.com"],
                subject="Data",
                body_text="Attached.",
                bcc=["carol@example.com"],
                attachments=[attachment],
            )

        self.assertTrue(delivery.archived)

        outgoing = message_from_bytes(smtp_server.data)
        self.assertIn(
            f"size={len(smtp_server.data)}".encode(), smtp_server.mail_from.lower()
        )
        self.assertEqual(len(smtp_server.recipients), 2)
        self.assertIsNone(outgoing["Bcc"])
        (part,) = [p for p in outgoing.walk() if p.get_filename()]
        self.assertEqual(part.get_payload(decode=True), PAYLOAD)

        self.assertIn(b'APPEND "Sent" (\\Seen) "', imap_server.append_command)
        archived = message_from_bytes(imap_server.literal)
        self.assertEqual(archived["Bcc"], "carol@example.com")
        self.assertEqual(archived["Message-ID"], outgoing["Message-ID"])
        self.assertTrue(imap_server.literal.endswith(smtp_server.data))
