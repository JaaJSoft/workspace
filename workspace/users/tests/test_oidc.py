from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.users.services.oidc import WorkspaceOIDCBackend

User = get_user_model()

# Minimal OP settings so OIDCAuthenticationBackend.__init__ (which reads these
# without defaults) does not raise when we instantiate the backend in tests.
OIDC_OP_SETTINGS = dict(
    OIDC_RP_CLIENT_ID='client',
    OIDC_RP_CLIENT_SECRET='secret',
    OIDC_OP_AUTHORIZATION_ENDPOINT='https://op.example/authorize',
    OIDC_OP_TOKEN_ENDPOINT='https://op.example/token',
    OIDC_OP_USER_ENDPOINT='https://op.example/userinfo',
    OIDC_OP_JWKS_ENDPOINT='https://op.example/jwks',
)


@override_settings(**OIDC_OP_SETTINGS)
class VerifyClaimsTests(TestCase):
    def setUp(self):
        self.backend = WorkspaceOIDCBackend()

    def test_passes_for_basic_email(self):
        self.assertTrue(self.backend.verify_claims({'email': 'jane@corp.com'}))

    def test_rejects_when_no_email(self):
        self.assertFalse(self.backend.verify_claims({'sub': 'x'}))

    @override_settings(OIDC_REQUIRE_EMAIL_VERIFIED=True)
    def test_requires_email_verified_when_enabled(self):
        self.assertFalse(self.backend.verify_claims({'email': 'a@corp.com'}))
        self.assertFalse(self.backend.verify_claims(
            {'email': 'a@corp.com', 'email_verified': False}))
        self.assertTrue(self.backend.verify_claims(
            {'email': 'a@corp.com', 'email_verified': True}))
        self.assertTrue(self.backend.verify_claims(
            {'email': 'a@corp.com', 'email_verified': 'true'}))

    def test_email_verified_not_imposed_by_default(self):
        self.assertTrue(self.backend.verify_claims(
            {'email': 'a@corp.com', 'email_verified': False}))

    @override_settings(OIDC_ALLOWED_DOMAINS=['corp.com'])
    def test_allowlist_rejects_outside_domain(self):
        self.assertFalse(self.backend.verify_claims({'email': 'a@evil.com'}))
        self.assertTrue(self.backend.verify_claims({'email': 'a@corp.com'}))

    def test_allowlist_empty_allows_any_domain(self):
        self.assertTrue(self.backend.verify_claims({'email': 'a@anything.io'}))

    def test_rejects_empty_email(self):
        # 'email' key present but empty: super() passes (key present), our own
        # guard rejects it.
        self.assertFalse(self.backend.verify_claims({'email': ''}))

    @override_settings(OIDC_ALLOWED_DOMAINS=['corp.com'])
    def test_allowlist_handles_non_string_email(self):
        # A non-conforming IdP returning a non-string email must be refused
        # cleanly, not raise a 500.
        self.assertFalse(self.backend.verify_claims({'email': 12345}))


@override_settings(**OIDC_OP_SETTINGS)
class GenerateUsernameTests(TestCase):
    def setUp(self):
        self.backend = WorkspaceOIDCBackend()

    def test_uses_preferred_username_by_default(self):
        u = self.backend._generate_username(
            {'preferred_username': 'jdoe', 'email': 'jdoe@corp.com'})
        self.assertEqual(u, 'jdoe')

    @override_settings(OIDC_USERNAME_CLAIM='sub')
    def test_uses_configured_claim(self):
        u = self.backend._generate_username(
            {'sub': 'xyz', 'preferred_username': 'ignored', 'email': 'a@corp.com'})
        self.assertEqual(u, 'xyz')

    def test_falls_back_to_email_localpart(self):
        u = self.backend._generate_username({'email': 'jane@corp.com'})
        self.assertEqual(u, 'jane')

    def test_falls_back_to_sub(self):
        u = self.backend._generate_username({'sub': 'sub-123'})
        self.assertEqual(u, 'sub-123')

    def test_sanitizes_disallowed_chars(self):
        u = self.backend._generate_username({'preferred_username': 'jean dupont!'})
        self.assertEqual(u, 'jeandupont')

    def test_dedupes_on_collision(self):
        User.objects.create_user('jdoe')
        u = self.backend._generate_username({'preferred_username': 'jdoe'})
        self.assertEqual(u, 'jdoe2')


@override_settings(**OIDC_OP_SETTINGS)
class CreateUserTests(TestCase):
    def setUp(self):
        self.backend = WorkspaceOIDCBackend()

    def test_creates_user_with_readable_username_and_profile(self):
        claims = {
            'preferred_username': 'jdoe',
            'email': 'jdoe@corp.com',
            'given_name': 'John',
            'family_name': 'Doe',
        }
        user = self.backend.create_user(claims)
        self.assertEqual(user.username, 'jdoe')        # readable, not a hash
        self.assertEqual(user.email, 'jdoe@corp.com')
        self.assertEqual(user.first_name, 'John')
        self.assertEqual(user.last_name, 'Doe')

    def test_update_user_not_overridden(self):
        # Must stay the library's no-op so local profile edits are not
        # clobbered on every login.
        self.assertNotIn('update_user', WorkspaceOIDCBackend.__dict__)


from django.conf import settings as dj_settings  # noqa: E402


class OidcSettingsWiringTests(TestCase):
    def test_oidc_disabled_by_default(self):
        self.assertFalse(dj_settings.OIDC_ENABLED)

    def test_oidc_backend_absent_when_disabled(self):
        # Critical: an unconfigured OIDC backend would raise ImproperlyConfigured
        # on every local login. It must NOT be in the list when disabled.
        self.assertNotIn(
            'workspace.users.services.oidc.WorkspaceOIDCBackend',
            dj_settings.AUTHENTICATION_BACKENDS,
        )
        self.assertIn(
            'django.contrib.auth.backends.ModelBackend',
            dj_settings.AUTHENTICATION_BACKENDS,
        )

    def test_oidc_app_installed(self):
        self.assertIn('mozilla_django_oidc', dj_settings.INSTALLED_APPS)

    def test_secure_and_required_defaults(self):
        self.assertEqual(dj_settings.OIDC_RP_SIGN_ALGO, 'RS256')
        self.assertIn('profile', dj_settings.OIDC_RP_SCOPES)
        self.assertFalse(dj_settings.OIDC_REQUIRE_EMAIL_VERIFIED)
        self.assertEqual(dj_settings.OIDC_USERNAME_CLAIM, 'preferred_username')


from django.urls import reverse  # noqa: E402


class LoginPageOidcButtonTests(TestCase):
    def test_button_hidden_when_disabled(self):
        resp = self.client.get(reverse('login'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'oidc-login-button')
        # Local username/password form is still rendered.
        self.assertContains(resp, 'name="username"')

    @override_settings(OIDC_ENABLED=True, OIDC_PROVIDER_NAME='Keycloak')
    def test_button_shown_when_enabled(self):
        resp = self.client.get(reverse('login'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'oidc-login-button')
        self.assertContains(resp, 'Sign in with Keycloak')
        self.assertContains(resp, reverse('oidc_authentication_init'))
