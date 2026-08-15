"""End-to-end tests for the OIDC (SSO) login flow.

Unlike ``users/tests/test_oidc.py``, which calls the backend directly with
hand-written claim dicts, these tests run the real thing: an OpenID Connect
provider (``oidc-provider-mock``, RS256 + JWKS + discovery) is booted in a
background thread, and a browser walks the whole round-trip - login page →
``/oidc/authenticate`` → the provider's authorization page → ``/oidc/callback``
→ authenticated session. That covers everything the unit tests cannot: the
redirect_uri the app builds from its own URLconf, state/nonce handling, the
token exchange, JWKS signature verification and the userinfo call.

The provider is a test double, so this validates *our* integration, not
interoperability with any particular IdP (Keycloak, Authentik, Entra...).

Skipped unless ``E2E=1`` is set (see the base class docstring).
"""

from __future__ import annotations

import httpx
from django.contrib.auth import get_user_model
from django.test import override_settings

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.users.models import OIDCIdentity

User = get_user_model()

JANE = {
    "sub": "op-subject-jane",
    "email": "jane.doe@corp.example",
    "email_verified": True,
    "preferred_username": "jane.doe",
    "given_name": "Jane",
    "family_name": "Doe",
}
# Same email as Jane, different subject: a recycled address, or someone trying
# to inherit her account through the IdP.
IMPOSTOR = {
    "sub": "op-subject-impostor",
    "email": "jane.doe@corp.example",
    "email_verified": True,
    "preferred_username": "jane.impostor",
}
UNVERIFIED = {
    "sub": "op-subject-bob",
    "email": "bob@corp.example",
    "email_verified": False,
    "preferred_username": "bob",
}


class OidcLoginE2ETests(PlaywrightTestCase):
    """Full browser round-trip against a live OpenID Connect provider."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Imported lazily: oidc-provider-mock is a dev dependency and this
        # module is imported even when E2E is off (the class is just skipped).
        import oidc_provider_mock

        cls._op_context = oidc_provider_mock.run_server_in_thread(
            user_claims=[
                oidc_provider_mock.User(sub=claims["sub"], claims=claims)
                for claims in (JANE, IMPOSTOR, UNVERIFIED)
            ],
        )
        server = cls._op_context.__enter__()
        issuer = cls.idp_url = f"http://localhost:{server.server_port}"
        # OIDC_ENABLED and AUTHENTICATION_BACKENDS are computed at settings
        # import time, so overriding the endpoints alone would leave the backend
        # unwired: both have to be set explicitly here.
        cls._settings = override_settings(
            OIDC_ENABLED=True,
            OIDC_PROVIDER_NAME="Test IdP",
            OIDC_RP_CLIENT_ID="workspace-e2e",
            OIDC_RP_CLIENT_SECRET="workspace-e2e-secret",
            OIDC_OP_AUTHORIZATION_ENDPOINT=f"{issuer}/oauth2/authorize",
            OIDC_OP_TOKEN_ENDPOINT=f"{issuer}/oauth2/token",
            OIDC_OP_USER_ENDPOINT=f"{issuer}/userinfo",
            OIDC_OP_JWKS_ENDPOINT=f"{issuer}/jwks",
            AUTHENTICATION_BACKENDS=[
                "workspace.users.services.oidc.WorkspaceOIDCBackend",
                "django.contrib.auth.backends.ModelBackend",
            ],
        )
        cls._settings.enable()

    @classmethod
    def tearDownClass(cls):
        try:
            cls._settings.disable()
            cls._op_context.__exit__(None, None, None)
        finally:
            super().tearDownClass()

    # ---- helpers ---------------------------------------------------------

    def sign_in_with_idp(self, sub):
        """Click the SSO button and authorize as ``sub`` on the provider."""
        self.page.goto(f"{self.live_server_url}/login")
        self.page.locator("#oidc-login-button").click()
        # The provider lists its known subjects as authorize buttons.
        self.page.locator(f"button[name='sub'][value='{sub}']").click()
        self.page.wait_for_load_state("networkidle")

    def set_idp_claims(self, sub, claims):
        """Change what the provider returns for ``sub`` (its runtime user API).

        Deliberately not driven through ``self.page.request``: this also runs
        from ``addCleanup``, which fires after the browser context is closed.
        """
        response = httpx.put(
            f"{self.idp_url}/users/{sub}", json=claims, trust_env=False
        )
        self.assertEqual(response.status_code, 204, response.text)

    def current_username(self):
        """Username behind the browser session, or None when anonymous."""
        response = self.page.request.get(f"{self.live_server_url}/api/v1/users/me")
        if response.status != 200:
            return None
        return response.json()["username"]

    # ---- tests -----------------------------------------------------------

    def test_first_login_provisions_the_account(self):
        self.sign_in_with_idp(JANE["sub"])

        self.assertNotIn("/login", self.page.url)
        self.assertEqual(self.current_username(), "jane.doe")

        user = User.objects.get(username="jane.doe")
        self.assertEqual(user.email, JANE["email"])
        self.assertEqual((user.first_name, user.last_name), ("Jane", "Doe"))
        # The IdP owns the credentials: no local password to brute-force.
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.oidc_identity.sub, JANE["sub"])

    def test_second_login_reuses_the_account_and_resyncs_the_name(self):
        self.sign_in_with_idp(JANE["sub"])
        User.objects.filter(username="jane.doe").update(first_name="Stale")

        self.context.clear_cookies()
        self.sign_in_with_idp(JANE["sub"])

        self.assertEqual(self.current_username(), "jane.doe")
        self.assertEqual(User.objects.filter(email=JANE["email"]).count(), 1)
        self.assertEqual(User.objects.get(username="jane.doe").first_name, "Jane")

    def test_subject_change_on_a_known_email_is_refused(self):
        self.sign_in_with_idp(JANE["sub"])
        self.context.clear_cookies()

        self.sign_in_with_idp(IMPOSTOR["sub"])

        self.assertIsNone(self.current_username())
        # The original link is untouched and no second account was created.
        self.assertEqual(
            OIDCIdentity.objects.get(user__username="jane.doe").sub, JANE["sub"]
        )
        self.assertEqual(User.objects.filter(email=JANE["email"]).count(), 1)

    def test_email_change_at_the_idp_keeps_the_same_account(self):
        self.sign_in_with_idp(JANE["sub"])
        self.context.clear_cookies()
        # The provider outlives each test, so put the original claims back.
        self.addCleanup(self.set_idp_claims, JANE["sub"], JANE)
        self.set_idp_claims(JANE["sub"], JANE | {"email": "jane.moved@corp.example"})

        self.sign_in_with_idp(JANE["sub"])

        self.assertEqual(self.current_username(), "jane.doe")
        self.assertEqual(User.objects.filter(username__startswith="jane").count(), 1)
        self.assertEqual(
            User.objects.get(username="jane.doe").email, "jane.moved@corp.example"
        )

    def test_domain_outside_the_allowlist_is_refused(self):
        with override_settings(OIDC_ALLOWED_DOMAINS=["other.example"]):
            self.sign_in_with_idp(JANE["sub"])

            self.assertIsNone(self.current_username())
            self.assertFalse(User.objects.filter(email=JANE["email"]).exists())

    def test_unverified_email_is_refused_when_required(self):
        with override_settings(OIDC_REQUIRE_EMAIL_VERIFIED=True):
            self.sign_in_with_idp(UNVERIFIED["sub"])

            self.assertIsNone(self.current_username())
            self.assertFalse(User.objects.filter(email=UNVERIFIED["email"]).exists())

    def test_local_login_still_works_while_sso_is_enabled(self):
        self.create_user(username="local", password="localpass123")

        self.login_via_ui("local", "localpass123")

        self.assertEqual(self.current_username(), "local")

    def test_sso_account_cannot_change_its_password(self):
        self.sign_in_with_idp(JANE["sub"])

        self.page.goto(f"{self.live_server_url}/users/settings#security")
        self.page.wait_for_load_state("networkidle")

        self.assertNotIn("changePasswordForm()", self.page.content())
        response = self.page.request.post(
            f"{self.live_server_url}/api/v1/users/me/password",
            data={"current_password": "whatever", "new_password": "NewPass!9xyz"},
            headers={"X-CSRFToken": self._csrf_token()},
        )
        self.assertEqual(response.status, 403)

    def _csrf_token(self):
        for cookie in self.context.cookies():
            if cookie["name"] == "csrftoken":
                return cookie["value"]
        return ""
