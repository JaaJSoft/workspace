"""What `connect_imap` and `connect_smtp` put on the wire, against loopback servers.

Each server records every command it reads and whether TLS was up when it
read it, so the tests can tell credentials sent over TLS from credentials sent
in clear. Its certificate is self-signed: a client trusts it only when the test
hands it a context that does, which is how a man in the middle looks to a
client that verifies certificates.
"""

import datetime
import ipaddress
import socketserver
import ssl
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.contrib.auth import get_user_model
from django.test import TestCase
from imapclient.exceptions import IMAPClientError, LoginError

from workspace.mail.models import MailAccount
from workspace.mail.services.imap_connection import connect_imap
from workspace.mail.services.smtp import connect_smtp

User = get_user_model()

PASSWORD = "s3cret-password"


def _self_signed_cert(test):
    """Paths of a certificate and key valid for 127.0.0.1."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
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
    return cert_path, key_path


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A client refusing the certificate aborts the handshake on this side.
        self.errors += 1


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.tls = False
        try:
            if self.server.implicit_tls:
                self.start_tls()
            self.converse()
        finally:
            self.server.hung_up.set()

    def start_tls(self):
        self.wfile.flush()
        self.connection = self.server.tls_context.wrap_socket(
            self.connection, server_side=True
        )
        self.rfile = self.connection.makefile("rb")
        self.wfile = self.connection.makefile("wb", buffering=0)
        self.tls = True

    def record(self, line):
        self.server.received.append((self.tls, line))


class _IMAPHandler(_Handler):
    def converse(self):
        self.wfile.write(b"* OK ready\r\n")
        while line := self.rfile.readline():
            self.record(line)
            tag, command = line.split(b" ", 2)[:2]
            command = command.strip().upper()
            if command == b"CAPABILITY":
                capabilities = b"IMAP4rev1 AUTH=PLAIN"
                if self.server.offers_starttls and not self.tls:
                    capabilities += b" STARTTLS"
                self.wfile.write(
                    b"* CAPABILITY " + capabilities + b"\r\n" + tag + b" OK done\r\n"
                )
            elif command == b"STARTTLS":
                self.wfile.write(tag + b" OK begin TLS\r\n")
                self.start_tls()
            elif command == b"LOGIN":
                if self.server.refuses_login:
                    self.wfile.write(tag + b" NO [AUTHENTICATIONFAILED] no\r\n")
                else:
                    self.wfile.write(tag + b" OK logged in\r\n")
            elif command == b"LOGOUT":
                self.wfile.write(b"* BYE\r\n" + tag + b" OK done\r\n")
                return
            else:
                self.wfile.write(tag + b" BAD unknown\r\n")


class _SMTPHandler(_Handler):
    def converse(self):
        self.wfile.write(b"220 localhost ready\r\n")
        while line := self.rfile.readline():
            self.record(line)
            command = line[:8].split(b" ")[0].strip().upper()
            if command == b"EHLO":
                starttls = b"250-STARTTLS\r\n" if not self.tls else b""
                self.wfile.write(
                    b"250-localhost\r\n" + starttls + b"250 AUTH PLAIN\r\n"
                )
            elif command == b"STARTTLS":
                self.wfile.write(b"220 go ahead\r\n")
                self.start_tls()
            elif command == b"AUTH":
                self.wfile.write(b"235 authenticated\r\n")
            elif command == b"QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"502 unknown\r\n")


def _serve(
    test, handler, *, implicit_tls=False, offers_starttls=True, refuses_login=False
):
    cert_path, key_path = _self_signed_cert(test)
    server = _Server(("127.0.0.1", 0), handler)
    server.received = []
    server.errors = 0
    server.refuses_login = refuses_login
    server.hung_up = threading.Event()
    server.implicit_tls = implicit_tls
    server.offers_starttls = offers_starttls
    server.tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.tls_context.load_cert_chain(cert_path, key_path)
    server.cert_path = cert_path
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server


def _trusting(server):
    """Make the client trust *server*'s certificate, and only that one."""
    return patch(
        "workspace.mail.services.tls.client_context",
        lambda: ssl.create_default_context(cafile=server.cert_path),
    )


class _AccountMixin:
    def setUp(self):
        user = User.objects.create_user(username="wire", password="pw")
        self.account = MailAccount.objects.create(
            owner=user,
            email="me@example.com",
            imap_host="127.0.0.1",
            smtp_host="127.0.0.1",
            username="me@example.com",
        )
        self.account.set_password(PASSWORD)
        self.account.save(update_fields=["password_encrypted"])

    def assertNothingSentInClear(self, server, verb):
        for tls, line in server.received:
            if not tls:
                self.assertNotIn(PASSWORD.encode(), line)
                self.assertNotIn(verb, line.upper())


class ConnectImapSecurityTests(_AccountMixin, TestCase):
    def _connect(self, server, *, implicit_tls):
        self.account.imap_use_ssl = implicit_tls
        self.account.imap_port = server.server_address[1]
        return connect_imap(self.account)

    @staticmethod
    def _imap_commands(server):
        return [
            (tls, line.split(b" ", 2)[1].strip().upper())
            for tls, line in server.received
        ]

    def test_starttls_upgrades_before_logging_in(self):
        server = _serve(self, _IMAPHandler)

        with _trusting(server):
            self._connect(server, implicit_tls=False).logout()

        commands = self._imap_commands(server)
        self.assertIn((False, b"STARTTLS"), commands)
        self.assertIn((True, b"LOGIN"), commands)
        self.assertNothingSentInClear(server, b"LOGIN")

    def test_starttls_refused_when_the_server_does_not_offer_it(self):
        server = _serve(self, _IMAPHandler, offers_starttls=False)

        with _trusting(server), self.assertRaises(IMAPClientError):
            self._connect(server, implicit_tls=False)

        self.assertNotIn(
            b"LOGIN", [command for _, command in self._imap_commands(server)]
        )
        self.assertNothingSentInClear(server, b"LOGIN")

    def test_starttls_refuses_an_untrusted_certificate(self):
        server = _serve(self, _IMAPHandler)

        with self.assertRaises(ssl.SSLCertVerificationError):
            self._connect(server, implicit_tls=False)

        self.assertNotIn(
            b"LOGIN", [command for _, command in self._imap_commands(server)]
        )

    def test_implicit_tls_logs_in_over_a_trusted_certificate(self):
        server = _serve(self, _IMAPHandler, implicit_tls=True)

        with _trusting(server):
            self._connect(server, implicit_tls=True).logout()

        self.assertIn((True, b"LOGIN"), self._imap_commands(server))

    def test_implicit_tls_refuses_an_untrusted_certificate(self):
        server = _serve(self, _IMAPHandler, implicit_tls=True)

        with self.assertRaises(ssl.SSLCertVerificationError):
            self._connect(server, implicit_tls=True)

        self.assertEqual(server.received, [])

    def test_a_refused_login_hangs_up(self):
        """Left open, every failed attempt would hold a socket until the
        server's own timeout."""
        server = _serve(self, _IMAPHandler, implicit_tls=True, refuses_login=True)

        with _trusting(server), self.assertRaises(LoginError):
            self._connect(server, implicit_tls=True)

        self.assertTrue(server.hung_up.wait(timeout=5))


class ConnectSmtpSecurityTests(_AccountMixin, TestCase):
    def _connect(self, server, *, starttls):
        self.account.smtp_use_tls = starttls
        self.account.smtp_port = server.server_address[1]
        return connect_smtp(self.account)

    @staticmethod
    def _smtp_commands(server):
        return [
            (tls, line.split(b" ")[0].strip().upper()) for tls, line in server.received
        ]

    def test_starttls_upgrades_before_authenticating(self):
        server = _serve(self, _SMTPHandler)

        with _trusting(server):
            self._connect(server, starttls=True).quit()

        commands = self._smtp_commands(server)
        self.assertIn((False, b"STARTTLS"), commands)
        self.assertIn((True, b"AUTH"), commands)
        self.assertNothingSentInClear(server, b"AUTH")

    def test_starttls_refuses_an_untrusted_certificate(self):
        server = _serve(self, _SMTPHandler)

        with self.assertRaises(ssl.SSLCertVerificationError):
            self._connect(server, starttls=True)

        self.assertNotIn(
            b"AUTH", [command for _, command in self._smtp_commands(server)]
        )

    def test_implicit_tls_authenticates_over_a_trusted_certificate(self):
        server = _serve(self, _SMTPHandler, implicit_tls=True)

        with _trusting(server):
            self._connect(server, starttls=False).quit()

        self.assertIn((True, b"AUTH"), self._smtp_commands(server))

    def test_implicit_tls_refuses_an_untrusted_certificate(self):
        server = _serve(self, _SMTPHandler, implicit_tls=True)

        with self.assertRaises(ssl.SSLCertVerificationError):
            self._connect(server, starttls=False)

        self.assertEqual(server.received, [])
