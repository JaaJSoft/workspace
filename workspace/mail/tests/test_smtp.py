from email import message_from_string
from unittest.mock import MagicMock, patch

from django.test import TestCase

from workspace.mail.services.credentials import decrypt, encrypt
from workspace.mail.services.smtp import (
    build_draft_message,
    connect_smtp,
    send_email,
    test_smtp_connection,
)


# ── credentials ─────────────────────────────────────────────────

class CredentialsTests(TestCase):

    def test_encrypt_decrypt_roundtrip(self):
        plaintext = 'my-secret-password'
        ciphertext = encrypt(plaintext)
        self.assertIsInstance(ciphertext, bytes)
        self.assertNotEqual(ciphertext, plaintext.encode())
        self.assertEqual(decrypt(ciphertext), plaintext)

    def test_encrypt_produces_different_ciphertexts(self):
        """Fernet uses a timestamp/IV, so each encryption should differ."""
        c1 = encrypt('same')
        c2 = encrypt('same')
        self.assertNotEqual(c1, c2)

    def test_decrypt_is_inverse_of_encrypt(self):
        for text in ['', 'short', 'x' * 1000, 'unicodé 🎉']:
            self.assertEqual(decrypt(encrypt(text)), text)


# ── build_draft_message ─────────────────────────────────────────

class BuildDraftMessageTests(TestCase):

    def _make_account(self):
        acct = MagicMock()
        acct.display_name = 'Alice'
        acct.email = 'alice@example.com'
        return acct

    def test_basic_message_structure(self):
        acct = self._make_account()
        raw = build_draft_message(
            acct, to=['bob@example.com'], subject='Hello',
            body_text='Hi Bob',
        )
        msg = message_from_string(raw.decode('utf-8'))
        self.assertEqual(msg['To'], 'bob@example.com')
        self.assertEqual(msg['Subject'], 'Hello')
        self.assertIn('alice@example.com', msg['From'])

    def test_cc_header(self):
        acct = self._make_account()
        raw = build_draft_message(
            acct, to=['bob@example.com'], subject='Test',
            cc=['carol@example.com'],
        )
        msg = message_from_string(raw.decode('utf-8'))
        self.assertEqual(msg['Cc'], 'carol@example.com')

    def test_html_body(self):
        acct = self._make_account()
        raw = build_draft_message(
            acct, to=['bob@example.com'], subject='HTML',
            body_html='<h1>Hello</h1>',
        )
        msg = message_from_string(raw.decode('utf-8'))
        # HTML body is in the multipart/alternative part, possibly base64-encoded
        self.assertIn('text/html', msg.as_string())

    def test_reply_to_header(self):
        acct = self._make_account()
        raw = build_draft_message(
            acct, to=['bob@example.com'], subject='Re',
            reply_to='noreply@example.com',
        )
        msg = message_from_string(raw.decode('utf-8'))
        self.assertEqual(msg['Reply-To'], 'noreply@example.com')

    def test_message_id_uses_domain(self):
        acct = self._make_account()
        raw = build_draft_message(acct, to=['bob@example.com'], subject='Test')
        msg = message_from_string(raw.decode('utf-8'))
        self.assertIn('example.com', msg['Message-ID'])

    def test_attachments(self):
        acct = self._make_account()
        attachment = MagicMock()
        attachment.name = 'doc.pdf'
        attachment.read.return_value = b'%PDF-fake'
        raw = build_draft_message(
            acct, to=['bob@example.com'], subject='With attachment',
            attachments=[attachment],
        )
        self.assertIn(b'doc.pdf', raw)


# ── connect_smtp ────────────────────────────────────────────────

class ConnectSmtpTests(TestCase):

    @patch('workspace.mail.services.smtp.smtplib.SMTP')
    def test_tls_connection(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server
        acct = MagicMock()
        acct.smtp_use_tls = True
        acct.smtp_host = 'smtp.example.com'
        acct.smtp_port = 587
        acct.auth_method = 'password'
        acct.username = 'alice'
        acct.get_password.return_value = 'secret'

        server = connect_smtp(acct)

        mock_smtp_cls.assert_called_with('smtp.example.com', 587)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_with('alice', 'secret')
        self.assertEqual(server, mock_server)

    @patch('workspace.mail.services.smtp.smtplib.SMTP_SSL')
    def test_ssl_connection(self, mock_smtp_ssl_cls):
        mock_server = MagicMock()
        mock_smtp_ssl_cls.return_value = mock_server
        acct = MagicMock()
        acct.smtp_use_tls = False
        acct.smtp_host = 'smtp.example.com'
        acct.smtp_port = 465
        acct.auth_method = 'password'
        acct.username = 'alice'
        acct.get_password.return_value = 'secret'

        server = connect_smtp(acct)

        mock_smtp_ssl_cls.assert_called_with('smtp.example.com', 465)
        mock_server.login.assert_called_with('alice', 'secret')


# ── test_smtp_connection ────────────────────────────────────────

class TestSmtpConnectionTests(TestCase):

    @patch('workspace.mail.services.smtp.connect_smtp')
    def test_success(self, mock_connect):
        mock_server = MagicMock()
        mock_connect.return_value = mock_server
        success, err = test_smtp_connection(MagicMock())
        self.assertTrue(success)
        self.assertIsNone(err)
        mock_server.quit.assert_called_once()

    @patch('workspace.mail.services.smtp.connect_smtp')
    def test_failure(self, mock_connect):
        mock_connect.side_effect = Exception('Connection refused')
        success, err = test_smtp_connection(MagicMock())
        self.assertFalse(success)
        self.assertIn('Connection refused', err)


# ── send_email ──────────────────────────────────────────────────

class SendEmailTests(TestCase):

    @patch('workspace.mail.services.smtp.connect_smtp')
    def test_sends_to_all_recipients(self, mock_connect):
        mock_server = MagicMock()
        mock_connect.return_value = mock_server
        acct = MagicMock()
        acct.display_name = 'Alice'
        acct.email = 'alice@example.com'

        send_email(
            acct, to=['bob@example.com'], subject='Test',
            body_text='Hello', cc=['carol@example.com'],
        )

        mock_server.sendmail.assert_called_once()
        call_args = mock_server.sendmail.call_args
        recipients = call_args[0][1]
        self.assertIn('bob@example.com', recipients)
        self.assertIn('carol@example.com', recipients)
        mock_server.quit.assert_called_once()
