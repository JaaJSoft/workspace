import time
import time as _time
from unittest.mock import MagicMock, patch

from authlib.integrations.base_client.errors import OAuthError
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from workspace.mail.models import MailAccount

User = get_user_model()


class OAuth2DataModelTests(TestCase):
    """Tests for MailAccount.set_oauth2_data / get_oauth2_data."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="oauthuser",
            email="oauth@test.com",
            password="pass123",
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="oauth@test.com",
            imap_host="imap.test.com",
            smtp_host="smtp.test.com",
            username="oauth@test.com",
        )

    def test_round_trip(self):
        """set_oauth2_data + get_oauth2_data preserves the dict."""
        payload = {
            "access_token": "tok_abc",
            "refresh_token": "ref_xyz",
            "expires_at": 1700000000,
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
        payload = {"access_token": "very_secret_token_12345"}
        self.account.set_oauth2_data(payload)
        self.account.save()
        self.account.refresh_from_db()

        raw = bytes(self.account.oauth2_data_encrypted)
        self.assertNotIn(b"very_secret_token_12345", raw)


class GetAvailableProvidersTests(TestCase):
    """Tests for oauth2.get_available_providers."""

    @override_settings(
        OAUTH_GOOGLE_CLIENT_ID="gid",
        OAUTH_GOOGLE_CLIENT_SECRET="gsec",
        OAUTH_MICROSOFT_CLIENT_ID="",
        OAUTH_MICROSOFT_CLIENT_SECRET="",
        OAUTH_GENERIC_CLIENT_ID="",
        OAUTH_GENERIC_CLIENT_SECRET="",
    )
    def test_returns_configured_providers(self):
        from workspace.mail.services.oauth2 import get_available_providers

        providers = get_available_providers()
        ids = [p["provider"] for p in providers]
        self.assertEqual(ids, ["google"])
        self.assertEqual(providers[0]["name"], "Google")

    @override_settings(
        OAUTH_GOOGLE_CLIENT_ID="gid",
        OAUTH_GOOGLE_CLIENT_SECRET="gsec",
        OAUTH_MICROSOFT_CLIENT_ID="mid",
        OAUTH_MICROSOFT_CLIENT_SECRET="msec",
        OAUTH_GENERIC_CLIENT_ID="",
        OAUTH_GENERIC_CLIENT_SECRET="",
    )
    def test_returns_multiple_providers(self):
        from workspace.mail.services.oauth2 import get_available_providers

        providers = get_available_providers()
        ids = [p["provider"] for p in providers]
        self.assertEqual(ids, ["google", "microsoft"])

    @override_settings(
        OAUTH_GOOGLE_CLIENT_ID="",
        OAUTH_GOOGLE_CLIENT_SECRET="",
        OAUTH_MICROSOFT_CLIENT_ID="",
        OAUTH_MICROSOFT_CLIENT_SECRET="",
        OAUTH_GENERIC_CLIENT_ID="",
        OAUTH_GENERIC_CLIENT_SECRET="",
    )
    def test_returns_empty_when_none_configured(self):
        from workspace.mail.services.oauth2 import get_available_providers

        self.assertEqual(get_available_providers(), [])


class GetProviderConfigTests(TestCase):
    """Tests for oauth2.get_provider_config."""

    def test_google_config(self):
        from workspace.mail.services.oauth2 import get_provider_config

        cfg = get_provider_config("google")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["name"], "Google")
        self.assertIn("accounts.google.com", cfg["auth_url"])
        self.assertEqual(cfg["imap_host"], "imap.gmail.com")
        self.assertEqual(cfg["imap_port"], 993)
        self.assertTrue(cfg["imap_use_ssl"])
        self.assertEqual(cfg["smtp_host"], "smtp.gmail.com")
        self.assertEqual(cfg["smtp_port"], 587)
        self.assertTrue(cfg["smtp_use_tls"])

    def test_microsoft_config(self):
        from workspace.mail.services.oauth2 import get_provider_config

        cfg = get_provider_config("microsoft")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["name"], "Microsoft")
        self.assertIn("login.microsoftonline.com", cfg["auth_url"])
        self.assertEqual(cfg["imap_host"], "outlook.office365.com")
        self.assertEqual(cfg["smtp_host"], "smtp.office365.com")

    @override_settings(
        OAUTH_GENERIC_NAME="MyProvider",
        OAUTH_GENERIC_AUTH_URL="https://auth.example.com/authorize",
        OAUTH_GENERIC_TOKEN_URL="https://auth.example.com/token",
        OAUTH_GENERIC_SCOPES="openid email",
        OAUTH_GENERIC_IMAP_HOST="imap.example.com",
        OAUTH_GENERIC_SMTP_HOST="smtp.example.com",
    )
    def test_generic_config(self):
        from workspace.mail.services.oauth2 import get_provider_config

        cfg = get_provider_config("generic")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["name"], "MyProvider")
        self.assertEqual(cfg["auth_url"], "https://auth.example.com/authorize")
        self.assertEqual(cfg["token_url"], "https://auth.example.com/token")
        self.assertEqual(cfg["imap_host"], "imap.example.com")
        self.assertEqual(cfg["smtp_host"], "smtp.example.com")

    def test_unknown_provider_returns_none(self):
        from workspace.mail.services.oauth2 import get_provider_config

        self.assertIsNone(get_provider_config("unknown"))


class GetValidAccessTokenTests(TestCase):
    """Tests for oauth2.get_valid_access_token."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="tokenuser",
            email="token@test.com",
            password="pass123",
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="token@test.com",
            imap_host="imap.test.com",
            smtp_host="smtp.test.com",
            username="token@test.com",
            oauth2_provider="google",
        )

    def test_returns_none_when_no_data(self):
        from workspace.mail.services.oauth2 import get_valid_access_token

        self.assertIsNone(get_valid_access_token(self.account))

    def test_returns_token_when_valid(self):
        from workspace.mail.services.oauth2 import get_valid_access_token

        future = time.time() + 3600
        self.account.set_oauth2_data(
            {
                "access_token": "still_good",
                "refresh_token": "ref",
                "expires_at": future,
            }
        )
        self.account.save()
        self.account.refresh_from_db()

        token = get_valid_access_token(self.account)
        self.assertEqual(token, "still_good")

    @patch("workspace.mail.services.oauth2._refresh_token")
    def test_refreshes_when_expired(self, mock_refresh):
        from workspace.mail.services.oauth2 import get_valid_access_token

        mock_refresh.return_value = "new_access_token"

        expired = time.time() - 100
        self.account.set_oauth2_data(
            {
                "access_token": "old_token",
                "refresh_token": "ref",
                "expires_at": expired,
            }
        )
        self.account.save()
        self.account.refresh_from_db()

        token = get_valid_access_token(self.account)
        self.assertEqual(token, "new_access_token")
        mock_refresh.assert_called_once()

    @patch("workspace.mail.services.oauth2._refresh_token")
    def test_refreshes_within_buffer(self, mock_refresh):
        """Token expiring within 60s should trigger a refresh."""
        from workspace.mail.services.oauth2 import get_valid_access_token

        mock_refresh.return_value = "refreshed"

        almost_expired = time.time() + 30  # 30s left, below 60s buffer
        self.account.set_oauth2_data(
            {
                "access_token": "about_to_expire",
                "refresh_token": "ref",
                "expires_at": almost_expired,
            }
        )
        self.account.save()
        self.account.refresh_from_db()

        token = get_valid_access_token(self.account)
        self.assertEqual(token, "refreshed")
        mock_refresh.assert_called_once()


class ConnectImapOAuth2Test(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="imapuser", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@gmail.com",
            username="user@gmail.com",
            imap_host="imap.gmail.com",
            smtp_host="smtp.gmail.com",
            auth_method="oauth2",
            oauth2_provider="google",
        )
        self.account.set_oauth2_data(
            {
                "access_token": "test-token",
                "refresh_token": "refresh",
                "expires_at": _time.time() + 3600,
                "token_type": "Bearer",
            }
        )
        self.account.save()

    @patch("workspace.mail.services.imap_connection.IMAPClient")
    @patch(
        "workspace.mail.services.oauth2.get_valid_access_token",
        return_value="test-token",
    )
    def test_uses_xoauth2_for_oauth2_accounts(self, mock_token, mock_client):
        from workspace.mail.services.imap_connection import connect_imap

        connect_imap(self.account)

        client = mock_client.return_value
        client.oauth2_login.assert_called_once_with("user@gmail.com", "test-token")
        client.login.assert_not_called()

    @patch("workspace.mail.services.imap_connection.IMAPClient")
    @patch(
        "workspace.mail.services.oauth2.get_valid_access_token",
        return_value=None,
    )
    def test_without_a_token_nothing_is_sent_and_the_connection_is_closed(
        self, mock_token, mock_client
    ):
        from workspace.mail.services.imap_connection import connect_imap

        with self.assertRaises(RuntimeError):
            connect_imap(self.account)

        client = mock_client.return_value
        client.oauth2_login.assert_not_called()
        client.shutdown.assert_called_once()

    @patch("workspace.mail.services.imap_connection.IMAPClient")
    def test_uses_login_for_password_accounts(self, mock_client):
        self.account.auth_method = "password"
        self.account.set_password("mypass")
        self.account.save()
        from workspace.mail.services.imap_connection import connect_imap

        connect_imap(self.account)

        client = mock_client.return_value
        client.login.assert_called_once_with("user@gmail.com", "mypass")
        client.oauth2_login.assert_not_called()


class ConnectSmtpOAuth2Test(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="smtpuser", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@gmail.com",
            username="user@gmail.com",
            imap_host="imap.gmail.com",
            smtp_host="smtp.gmail.com",
            auth_method="oauth2",
            oauth2_provider="google",
        )
        self.account.set_oauth2_data(
            {
                "access_token": "smtp-token",
                "refresh_token": "refresh",
                "expires_at": _time.time() + 3600,
                "token_type": "Bearer",
            }
        )
        self.account.save()

    @patch("workspace.mail.services.smtp.smtplib")
    @patch(
        "workspace.mail.services.oauth2.get_valid_access_token",
        return_value="smtp-token",
    )
    def test_uses_xoauth2_for_oauth2_accounts(self, mock_token, mock_smtplib):
        mock_server = MagicMock()
        mock_smtplib.SMTP.return_value = mock_server
        from workspace.mail.services.smtp import connect_smtp

        connect_smtp(self.account)
        mock_server.login.assert_not_called()
        mock_server.docmd.assert_called_once()
        args = mock_server.docmd.call_args[0]
        self.assertEqual(args[0], "AUTH")
        self.assertIn("XOAUTH2", args[1])

    @patch("workspace.mail.services.smtp.smtplib")
    def test_uses_login_for_password_accounts(self, mock_smtplib):
        self.account.auth_method = "password"
        self.account.set_password("mypass")
        self.account.save()
        mock_server = MagicMock()
        mock_smtplib.SMTP.return_value = mock_server
        from workspace.mail.services.smtp import connect_smtp

        connect_smtp(self.account)
        mock_server.login.assert_called_once_with("user@gmail.com", "mypass")


class OAuthProvidersViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="provuser", password="pass")
        self.factory = RequestFactory()

    @override_settings(
        OAUTH_GOOGLE_CLIENT_ID="gid",
        OAUTH_GOOGLE_CLIENT_SECRET="gsec",
        OAUTH_MICROSOFT_CLIENT_ID="",
        OAUTH_MICROSOFT_CLIENT_SECRET="",
        OAUTH_GENERIC_CLIENT_ID="",
        OAUTH_GENERIC_CLIENT_SECRET="",
    )
    def test_returns_available_providers(self):
        from workspace.mail.views.oauth2 import OAuthProvidersView

        request = self.factory.get("/api/v1/mail/oauth2/providers")
        request.user = self.user
        response = OAuthProvidersView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["provider"], "google")


class OAuthCallbackTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cbuser", password="pass")
        self.client.force_login(self.user)

    def test_callback_error_from_provider(self):
        response = self.client.get(
            "/mail/oauth2/callback",
            {
                "error": "access_denied",
                "error_description": "User denied access",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User denied access")

    def test_callback_invalid_state(self):
        session = self.client.session
        session["oauth2_state"] = "valid-state"
        session["oauth2_provider"] = "google"
        session.save()
        response = self.client.get(
            "/mail/oauth2/callback",
            {
                "code": "test-code",
                "state": "wrong-state",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid state")


class RevokedTokenTests(TestCase):
    """Tests for invalid_grant handling in _refresh_token."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="revokeuser",
            email="revoke@test.com",
            password="pass123",
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="revoke@gmail.com",
            imap_host="imap.gmail.com",
            smtp_host="smtp.gmail.com",
            username="revoke@gmail.com",
            auth_method="oauth2",
            oauth2_provider="google",
            is_active=True,
        )
        self.account.set_oauth2_data(
            {
                "access_token": "old",
                "refresh_token": "expired_refresh",
                "expires_at": time.time() - 100,
            }
        )
        self.account.save()

    @override_settings(
        OAUTH_GOOGLE_CLIENT_ID="gid",
        OAUTH_GOOGLE_CLIENT_SECRET="gsec",
    )
    @patch("workspace.mail.services.oauth2.OAuth2Session.fetch_token")
    @patch("workspace.notifications.services.notifications.notify")
    def test_invalid_grant_deactivates_account(self, mock_notify, mock_fetch):
        from workspace.mail.services.oauth2 import _refresh_token

        mock_fetch.side_effect = OAuthError(
            error="invalid_grant",
            description="Token has been expired or revoked.",
        )

        data = self.account.get_oauth2_data()
        with self.assertRaises(OAuthError):
            _refresh_token(self.account, data)

        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)
        self.assertIn("reconnect", self.account.last_sync_error.lower())

    @override_settings(
        OAUTH_GOOGLE_CLIENT_ID="gid",
        OAUTH_GOOGLE_CLIENT_SECRET="gsec",
    )
    @patch("workspace.mail.services.oauth2.OAuth2Session.fetch_token")
    @patch("workspace.notifications.services.notifications.notify")
    def test_invalid_grant_sends_notification(self, mock_notify, mock_fetch):
        from workspace.mail.services.oauth2 import _refresh_token

        mock_fetch.side_effect = OAuthError(
            error="invalid_grant",
            description="Token has been expired or revoked.",
        )

        data = self.account.get_oauth2_data()
        with self.assertRaises(OAuthError):
            _refresh_token(self.account, data)

        mock_notify.assert_called_once()
        call_kwargs = mock_notify.call_args[1]
        self.assertEqual(call_kwargs["recipient"], self.user)
        self.assertEqual(call_kwargs["origin"], "mail")
        self.assertEqual(call_kwargs["priority"], "high")
        self.assertIn("revoke@gmail.com", call_kwargs["title"])

    @override_settings(
        OAUTH_GOOGLE_CLIENT_ID="gid",
        OAUTH_GOOGLE_CLIENT_SECRET="gsec",
    )
    @patch("workspace.mail.services.oauth2.OAuth2Session.fetch_token")
    def test_other_oauth_errors_not_deactivated(self, mock_fetch):
        from workspace.mail.services.oauth2 import _refresh_token

        mock_fetch.side_effect = OAuthError(
            error="server_error",
            description="Internal server error",
        )

        data = self.account.get_oauth2_data()
        with self.assertRaises(OAuthError):
            _refresh_token(self.account, data)

        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)  # not deactivated


class OAuthReconnectTest(TestCase):
    """Tests for reconnecting an existing deactivated account via OAuth2."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="reconnuser",
            password="pass",
        )
        self.client.force_login(self.user)

        # Existing deactivated account
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="reconnuser@gmail.com",
            username="reconnuser@gmail.com",
            imap_host="imap.gmail.com",
            smtp_host="smtp.gmail.com",
            auth_method="oauth2",
            oauth2_provider="google",
            is_active=False,
            last_sync_error="Authorization expired or revoked.",
        )

    @patch("workspace.mail.services.oauth2.fetch_userinfo")
    @patch("workspace.mail.views.oauth2.exchange_code")
    def test_callback_reactivates_existing_account(self, mock_exchange, mock_userinfo):
        mock_exchange.return_value = {
            "access_token": "new_tok",
            "refresh_token": "new_ref",
            "expires_at": time.time() + 3600,
        }
        mock_userinfo.return_value = {"email": "reconnuser@gmail.com"}

        session = self.client.session
        session["oauth2_state"] = "state123"
        session["oauth2_provider"] = "google"
        session.save()

        response = self.client.get(
            "/mail/oauth2/callback",
            {
                "code": "auth-code",
                "state": "state123",
            },
        )
        self.assertEqual(response.status_code, 200)

        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)
        self.assertEqual(self.account.last_sync_error, "")
        self.assertIsNotNone(self.account.get_oauth2_data())
        self.assertEqual(self.account.get_oauth2_data()["access_token"], "new_tok")

        # Should not have created a duplicate account
        count = MailAccount.objects.filter(
            owner=self.user,
            email="reconnuser@gmail.com",
        ).count()
        self.assertEqual(count, 1)


def _id_token(claims):
    """A compact JWS carrying *claims*, with a signature nothing checks."""
    from joserfc.util import json_b64encode

    header = json_b64encode({"alg": "RS256", "typ": "JWT"}).decode()
    payload = json_b64encode(claims).decode()
    return f"{header}.{payload}.c2lnbmF0dXJl"


@override_settings(
    OAUTH_GENERIC_CLIENT_ID="generic-client",
    OAUTH_GENERIC_CLIENT_SECRET="generic-secret",
    OAUTH_GENERIC_NAME="Example Mail",
    OAUTH_GENERIC_AUTH_URL="https://auth.example.com/authorize",
    OAUTH_GENERIC_TOKEN_URL="https://auth.example.com/token",
    OAUTH_GENERIC_USERINFO_URL="https://auth.example.com/userinfo",
    OAUTH_GENERIC_SCOPES="openid email",
    OAUTH_GENERIC_IMAP_HOST="imap.example.com",
    OAUTH_GENERIC_SMTP_HOST="smtp.example.com",
)
class GenericProviderCallbackTests(TestCase):
    """The callback completes a sign-in with the admin-configured provider."""

    def setUp(self):
        self.user = User.objects.create_user(username="genericuser", password="pass")
        self.client.force_login(self.user)
        session = self.client.session
        session["oauth2_state"] = "state-generic"
        session["oauth2_provider"] = "generic"
        session.save()

    def _callback(self, tokens, userinfo=None, userinfo_error=None):
        userinfo_response = MagicMock()
        userinfo_response.json.return_value = userinfo or {}
        if userinfo_error:
            userinfo_response.raise_for_status.side_effect = userinfo_error
        with (
            patch(
                "workspace.mail.services.oauth2.OAuth2Session.fetch_token",
                return_value=tokens,
            ) as fetch_token,
            patch(
                "workspace.mail.services.oauth2.OAuth2Session.get",
                return_value=userinfo_response,
            ) as get,
        ):
            response = self.client.get(
                "/mail/oauth2/callback",
                {"code": "auth-code", "state": "state-generic"},
            )
        return response, fetch_token, get

    def _tokens(self, **extra):
        return {
            "access_token": "generic-access",
            "refresh_token": "generic-refresh",
            "expires_at": time.time() + 3600,
            "token_type": "Bearer",
            **extra,
        }

    def test_email_from_userinfo_endpoint(self):
        response, fetch_token, get = self._callback(
            self._tokens(), userinfo={"email": "alice@example.com"}
        )

        self.assertEqual(response.context["result"]["type"], "oauth2-success")
        self.assertEqual(fetch_token.call_args[0][0], "https://auth.example.com/token")
        self.assertEqual(get.call_args[0][0], "https://auth.example.com/userinfo")
        account = MailAccount.objects.get(owner=self.user)
        self.assertEqual(account.email, "alice@example.com")
        self.assertEqual(account.oauth2_provider, "generic")
        self.assertEqual(account.imap_host, "imap.example.com")
        self.assertEqual(account.smtp_host, "smtp.example.com")
        self.assertEqual(account.get_oauth2_data()["access_token"], "generic-access")

    @override_settings(OAUTH_GENERIC_USERINFO_URL="")
    def test_email_from_id_token_without_userinfo_endpoint(self):
        tokens = self._tokens(
            id_token=_id_token({"aud": "generic-client", "email": "bob@example.com"})
        )

        response, _, get = self._callback(tokens)

        self.assertEqual(response.context["result"]["type"], "oauth2-success")
        get.assert_not_called()
        self.assertEqual(
            MailAccount.objects.get(owner=self.user).email, "bob@example.com"
        )

    def test_id_token_used_when_userinfo_has_no_email(self):
        tokens = self._tokens(
            id_token=_id_token(
                {"aud": ["generic-client"], "email": "carol@example.com"}
            )
        )

        response, _, _ = self._callback(tokens, userinfo={"sub": "123"})

        self.assertEqual(response.context["result"]["type"], "oauth2-success")
        self.assertEqual(
            MailAccount.objects.get(owner=self.user).email, "carol@example.com"
        )

    def test_id_token_used_when_userinfo_request_fails(self):
        import requests

        tokens = self._tokens(
            id_token=_id_token({"aud": "generic-client", "email": "dan@example.com"})
        )

        response, _, _ = self._callback(
            tokens, userinfo_error=requests.HTTPError("503 Server Error")
        )

        self.assertEqual(response.context["result"]["type"], "oauth2-success")
        self.assertEqual(
            MailAccount.objects.get(owner=self.user).email, "dan@example.com"
        )

    def test_id_token_used_when_userinfo_is_not_an_object(self):
        tokens = self._tokens(
            id_token=_id_token({"aud": "generic-client", "email": "fay@example.com"})
        )

        response, _, _ = self._callback(tokens, userinfo=["fay@example.com"])

        self.assertEqual(response.context["result"]["type"], "oauth2-success")
        self.assertEqual(
            MailAccount.objects.get(owner=self.user).email, "fay@example.com"
        )

    def test_id_token_used_when_userinfo_email_is_not_a_string(self):
        tokens = self._tokens(
            id_token=_id_token({"aud": "generic-client", "email": "gus@example.com"})
        )

        response, _, _ = self._callback(tokens, userinfo={"email": {"primary": True}})

        self.assertEqual(response.context["result"]["type"], "oauth2-success")
        self.assertEqual(
            MailAccount.objects.get(owner=self.user).email, "gus@example.com"
        )

    @override_settings(OAUTH_GENERIC_USERINFO_URL="")
    def test_id_token_for_another_client_is_ignored(self):
        tokens = self._tokens(
            id_token=_id_token({"aud": "someone-else", "email": "eve@example.com"})
        )

        response, _, _ = self._callback(tokens)

        self.assertEqual(
            response.context["result"],
            {
                "type": "oauth2-error",
                "error": "Could not determine email address from provider.",
            },
        )
        self.assertFalse(MailAccount.objects.filter(owner=self.user).exists())

    @override_settings(OAUTH_GENERIC_USERINFO_URL="")
    def test_malformed_id_token_is_ignored(self):
        response, _, _ = self._callback(self._tokens(id_token="not-a-jwt"))

        self.assertEqual(
            response.context["result"],
            {
                "type": "oauth2-error",
                "error": "Could not determine email address from provider.",
            },
        )
        self.assertFalse(MailAccount.objects.filter(owner=self.user).exists())
