import base64
import time
import time as _time
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from workspace.mail.models import MailAccount

User = get_user_model()


class OAuth2DataModelTests(TestCase):
    """Tests for MailAccount.set_oauth2_data / get_oauth2_data."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='oauthuser', email='oauth@test.com', password='pass123',
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='oauth@test.com',
            imap_host='imap.test.com',
            smtp_host='smtp.test.com',
            username='oauth@test.com',
        )

    def test_round_trip(self):
        """set_oauth2_data + get_oauth2_data preserves the dict."""
        payload = {
            'access_token': 'tok_abc',
            'refresh_token': 'ref_xyz',
            'expires_at': 1700000000,
        }
        self.account.set_oauth2_data(payload)
        self.account.save()
        self.account.refresh_from_db()

        result = self.account.get_oauth2_data()
        self.assertEqual(result, payload)

    def test_returns_none_when_empty(self):
        """get_oauth2_data returns None when no data has been stored."""
        self.assertIsNone(self.account.get_oauth2_data())

    def test_data_is_encrypted(self):
        """The raw binary field must not contain the plaintext token."""
        payload = {'access_token': 'very_secret_token_12345'}
        self.account.set_oauth2_data(payload)
        self.account.save()
        self.account.refresh_from_db()

        raw = bytes(self.account.oauth2_data_encrypted)
        self.assertNotIn(b'very_secret_token_12345', raw)


class GetAvailableProvidersTests(TestCase):
    """Tests for oauth2.get_available_providers."""

    @override_settings(
        OAUTH_GOOGLE_CLIENT_ID='gid',
        OAUTH_GOOGLE_CLIENT_SECRET='gsec',
        OAUTH_MICROSOFT_CLIENT_ID='',
        OAUTH_MICROSOFT_CLIENT_SECRET='',
        OAUTH_GENERIC_CLIENT_ID='',
        OAUTH_GENERIC_CLIENT_SECRET='',
    )
    def test_returns_configured_providers(self):
        from workspace.mail.services.oauth2 import get_available_providers

        providers = get_available_providers()
        ids = [p['provider'] for p in providers]
        self.assertEqual(ids, ['google'])
        self.assertEqual(providers[0]['name'], 'Google')

    @override_settings(
        OAUTH_GOOGLE_CLIENT_ID='gid',
        OAUTH_GOOGLE_CLIENT_SECRET='gsec',
        OAUTH_MICROSOFT_CLIENT_ID='mid',
        OAUTH_MICROSOFT_CLIENT_SECRET='msec',
        OAUTH_GENERIC_CLIENT_ID='',
        OAUTH_GENERIC_CLIENT_SECRET='',
    )
    def test_returns_multiple_providers(self):
        from workspace.mail.services.oauth2 import get_available_providers

        providers = get_available_providers()
        ids = [p['provider'] for p in providers]
        self.assertEqual(ids, ['google', 'microsoft'])

    @override_settings(
        OAUTH_GOOGLE_CLIENT_ID='',
        OAUTH_GOOGLE_CLIENT_SECRET='',
        OAUTH_MICROSOFT_CLIENT_ID='',
        OAUTH_MICROSOFT_CLIENT_SECRET='',
        OAUTH_GENERIC_CLIENT_ID='',
        OAUTH_GENERIC_CLIENT_SECRET='',
    )
    def test_returns_empty_when_none_configured(self):
        from workspace.mail.services.oauth2 import get_available_providers

        self.assertEqual(get_available_providers(), [])


class GetProviderConfigTests(TestCase):
    """Tests for oauth2.get_provider_config."""

    def test_google_config(self):
        from workspace.mail.services.oauth2 import get_provider_config

        cfg = get_provider_config('google')
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['name'], 'Google')
        self.assertIn('accounts.google.com', cfg['auth_url'])
        self.assertEqual(cfg['imap_host'], 'imap.gmail.com')
        self.assertEqual(cfg['imap_port'], 993)
        self.assertTrue(cfg['imap_use_ssl'])
        self.assertEqual(cfg['smtp_host'], 'smtp.gmail.com')
        self.assertEqual(cfg['smtp_port'], 587)
        self.assertTrue(cfg['smtp_use_tls'])

    def test_microsoft_config(self):
        from workspace.mail.services.oauth2 import get_provider_config

        cfg = get_provider_config('microsoft')
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['name'], 'Microsoft')
        self.assertIn('login.microsoftonline.com', cfg['auth_url'])
        self.assertEqual(cfg['imap_host'], 'outlook.office365.com')
        self.assertEqual(cfg['smtp_host'], 'smtp.office365.com')

    @override_settings(
        OAUTH_GENERIC_NAME='MyProvider',
        OAUTH_GENERIC_AUTH_URL='https://auth.example.com/authorize',
        OAUTH_GENERIC_TOKEN_URL='https://auth.example.com/token',
        OAUTH_GENERIC_SCOPES='openid email',
        OAUTH_GENERIC_IMAP_HOST='imap.example.com',
        OAUTH_GENERIC_SMTP_HOST='smtp.example.com',
    )
    def test_generic_config(self):
        from workspace.mail.services.oauth2 import get_provider_config

        cfg = get_provider_config('generic')
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['name'], 'MyProvider')
        self.assertEqual(cfg['auth_url'], 'https://auth.example.com/authorize')
        self.assertEqual(cfg['token_url'], 'https://auth.example.com/token')
        self.assertEqual(cfg['imap_host'], 'imap.example.com')
        self.assertEqual(cfg['smtp_host'], 'smtp.example.com')

    def test_unknown_provider_returns_none(self):
        from workspace.mail.services.oauth2 import get_provider_config

        self.assertIsNone(get_provider_config('unknown'))


class GetValidAccessTokenTests(TestCase):
    """Tests for oauth2.get_valid_access_token."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='tokenuser', email='token@test.com', password='pass123',
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='token@test.com',
            imap_host='imap.test.com',
            smtp_host='smtp.test.com',
            username='token@test.com',
            oauth2_provider='google',
        )

    def test_returns_none_when_no_data(self):
        from workspace.mail.services.oauth2 import get_valid_access_token

        self.assertIsNone(get_valid_access_token(self.account))

    def test_returns_token_when_valid(self):
        from workspace.mail.services.oauth2 import get_valid_access_token

        future = time.time() + 3600
        self.account.set_oauth2_data({
            'access_token': 'still_good',
            'refresh_token': 'ref',
            'expires_at': future,
        })
        self.account.save()
        self.account.refresh_from_db()

        token = get_valid_access_token(self.account)
        self.assertEqual(token, 'still_good')

    @patch('workspace.mail.services.oauth2._refresh_token')
    def test_refreshes_when_expired(self, mock_refresh):
        from workspace.mail.services.oauth2 import get_valid_access_token

        mock_refresh.return_value = 'new_access_token'

        expired = time.time() - 100
        self.account.set_oauth2_data({
            'access_token': 'old_token',
            'refresh_token': 'ref',
            'expires_at': expired,
        })
        self.account.save()
        self.account.refresh_from_db()

        token = get_valid_access_token(self.account)
        self.assertEqual(token, 'new_access_token')
        mock_refresh.assert_called_once()

    @patch('workspace.mail.services.oauth2._refresh_token')
    def test_refreshes_within_buffer(self, mock_refresh):
        """Token expiring within 60s should trigger a refresh."""
        from workspace.mail.services.oauth2 import get_valid_access_token

        mock_refresh.return_value = 'refreshed'

        almost_expired = time.time() + 30  # 30s left, below 60s buffer
        self.account.set_oauth2_data({
            'access_token': 'about_to_expire',
            'refresh_token': 'ref',
            'expires_at': almost_expired,
        })
        self.account.save()
        self.account.refresh_from_db()

        token = get_valid_access_token(self.account)
        self.assertEqual(token, 'refreshed')
        mock_refresh.assert_called_once()


class ConnectImapOAuth2Test(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='imapuser', password='pass')
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='user@gmail.com',
            username='user@gmail.com',
            imap_host='imap.gmail.com',
            smtp_host='smtp.gmail.com',
            auth_method='oauth2',
            oauth2_provider='google',
        )
        self.account.set_oauth2_data({
            'access_token': 'test-token',
            'refresh_token': 'refresh',
            'expires_at': _time.time() + 3600,
            'token_type': 'Bearer',
        })
        self.account.save()

    @patch('workspace.mail.services.imap.imaplib')
    @patch('workspace.mail.services.oauth2.get_valid_access_token', return_value='test-token')
    def test_uses_xoauth2_for_oauth2_accounts(self, mock_token, mock_imaplib):
        mock_conn = MagicMock()
        mock_imaplib.IMAP4_SSL.return_value = mock_conn
        from workspace.mail.services.imap import connect_imap
        connect_imap(self.account)
        mock_conn.authenticate.assert_called_once()
        args = mock_conn.authenticate.call_args
        self.assertEqual(args[0][0], 'XOAUTH2')
        callback = args[0][1]
        auth_bytes = callback(None)
        self.assertIn(b'user=user@gmail.com', auth_bytes)
        self.assertIn(b'auth=Bearer test-token', auth_bytes)

    @patch('workspace.mail.services.imap.imaplib')
    def test_uses_login_for_password_accounts(self, mock_imaplib):
        self.account.auth_method = 'password'
        self.account.set_password('mypass')
        self.account.save()
        mock_conn = MagicMock()
        mock_imaplib.IMAP4_SSL.return_value = mock_conn
        from workspace.mail.services.imap import connect_imap
        connect_imap(self.account)
        mock_conn.login.assert_called_once_with('user@gmail.com', 'mypass')
        mock_conn.authenticate.assert_not_called()


class ConnectSmtpOAuth2Test(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='smtpuser', password='pass')
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='user@gmail.com',
            username='user@gmail.com',
            imap_host='imap.gmail.com',
            smtp_host='smtp.gmail.com',
            auth_method='oauth2',
            oauth2_provider='google',
        )
        self.account.set_oauth2_data({
            'access_token': 'smtp-token',
            'refresh_token': 'refresh',
            'expires_at': _time.time() + 3600,
            'token_type': 'Bearer',
        })
        self.account.save()

    @patch('workspace.mail.services.smtp.smtplib')
    @patch('workspace.mail.services.oauth2.get_valid_access_token', return_value='smtp-token')
    def test_uses_xoauth2_for_oauth2_accounts(self, mock_token, mock_smtplib):
        mock_server = MagicMock()
        mock_smtplib.SMTP.return_value = mock_server
        from workspace.mail.services.smtp import connect_smtp
        connect_smtp(self.account)
        mock_server.login.assert_not_called()
        mock_server.docmd.assert_called_once()
        args = mock_server.docmd.call_args[0]
        self.assertEqual(args[0], 'AUTH')
        self.assertIn('XOAUTH2', args[1])

    @patch('workspace.mail.services.smtp.smtplib')
    def test_uses_login_for_password_accounts(self, mock_smtplib):
        self.account.auth_method = 'password'
        self.account.set_password('mypass')
        self.account.save()
        mock_server = MagicMock()
        mock_smtplib.SMTP.return_value = mock_server
        from workspace.mail.services.smtp import connect_smtp
        connect_smtp(self.account)
        mock_server.login.assert_called_once_with('user@gmail.com', 'mypass')


class OAuthProvidersViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='provuser', password='pass')
        self.factory = RequestFactory()

    @override_settings(
        OAUTH_GOOGLE_CLIENT_ID='gid',
        OAUTH_GOOGLE_CLIENT_SECRET='gsec',
        OAUTH_MICROSOFT_CLIENT_ID='',
        OAUTH_MICROSOFT_CLIENT_SECRET='',
        OAUTH_GENERIC_CLIENT_ID='',
        OAUTH_GENERIC_CLIENT_SECRET='',
    )
    def test_returns_available_providers(self):
        from workspace.mail.views_oauth2 import OAuthProvidersView

        request = self.factory.get('/api/v1/mail/oauth2/providers')
        request.user = self.user
        response = OAuthProvidersView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['provider'], 'google')


class OAuthCallbackTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cbuser', password='pass')
        self.client.force_login(self.user)

    def test_callback_error_from_provider(self):
        response = self.client.get('/mail/oauth2/callback', {
            'error': 'access_denied',
            'error_description': 'User denied access',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'User denied access')

    def test_callback_invalid_state(self):
        session = self.client.session
        session['oauth2_state'] = 'valid-state'
        session['oauth2_provider'] = 'google'
        session.save()
        response = self.client.get('/mail/oauth2/callback', {
            'code': 'test-code',
            'state': 'wrong-state',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Invalid state')
