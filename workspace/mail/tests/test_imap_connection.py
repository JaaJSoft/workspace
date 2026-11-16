"""What `connect_imap` puts on the wire, against a loopback IMAP server.

The server records every command it reads and whether TLS was up when it
read it, so the tests can tell a password sent over TLS from one sent in
clear.
"""

import datetime
import imaplib
import socketserver
import ssl
import tempfile
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount
from workspace.mail.services.imap_connection import connect_imap

User = get_user_model()

PASSWORD = "s3cret-password"


def _server_tls_context(test):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    cert_path = Path(directory.name) / "cert.pem"
    key_path = Path(directory.name) / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context


class _IMAPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        tls = False
        self.wfile.write(b"* OK ready\r\n")
        while line := self.rfile.readline():
            server.received.append((tls, line))
            tag, command = line.split(b" ", 2)[:2]
            command = command.strip().upper()
            if command == b"CAPABILITY":
                capabilities = b"IMAP4rev1 AUTH=PLAIN"
                if server.offers_starttls and not tls:
                    capabilities += b" STARTTLS"
                self.wfile.write(
                    b"* CAPABILITY " + capabilities + b"\r\n" + tag + b" OK done\r\n"
                )
            elif command == b"STARTTLS":
                self.wfile.write(tag + b" OK begin TLS\r\n")
                self.wfile.flush()
                self.connection = server.tls_context.wrap_socket(
                    self.connection, server_side=True
                )
                self.rfile = self.connection.makefile("rb")
                self.wfile = self.connection.makefile("wb", buffering=0)
                tls = True
            elif command == b"LOGIN":
                self.wfile.write(tag + b" OK logged in\r\n")
            elif command == b"LOGOUT":
                self.wfile.write(b"* BYE\r\n" + tag + b" OK done\r\n")
                return
            else:
                self.wfile.write(tag + b" BAD unknown\r\n")


def _serve(test, *, offers_starttls):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _IMAPHandler)
    server.daemon_threads = True
    server.received = []
    server.offers_starttls = offers_starttls
    server.tls_context = _server_tls_context(test)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server


class ConnectImapWithoutImplicitTlsTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="imapwire", password="pw")
        self.account = MailAccount.objects.create(
            owner=user,
            email="me@example.com",
            imap_host="127.0.0.1",
            imap_use_ssl=False,
            smtp_host="smtp.example.com",
            username="me@example.com",
        )
        self.account.set_password(PASSWORD)
        self.account.save(update_fields=["password_encrypted"])

    def _connect(self, server):
        self.account.imap_port = server.server_address[1]
        return connect_imap(self.account)

    def _commands(self, server):
        return [
            (tls, line.split(b" ", 2)[1].strip().upper())
            for tls, line in server.received
        ]

    def test_upgrades_with_starttls_before_logging_in(self):
        server = _serve(self, offers_starttls=True)

        conn = self._connect(server)
        conn.logout()

        commands = self._commands(server)
        self.assertIn((False, b"STARTTLS"), commands)
        self.assertIn((True, b"LOGIN"), commands)
        self.assertNotIn((False, b"LOGIN"), commands)
        self.assertFalse(
            any(PASSWORD.encode() in line for tls, line in server.received if not tls)
        )

    def test_refuses_to_log_in_when_the_server_offers_no_starttls(self):
        server = _serve(self, offers_starttls=False)

        with self.assertRaises(imaplib.IMAP4.error):
            self._connect(server)

        self.assertNotIn(b"LOGIN", [command for _, command in self._commands(server)])
        self.assertFalse(any(PASSWORD.encode() in line for _, line in server.received))
