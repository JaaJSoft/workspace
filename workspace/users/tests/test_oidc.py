from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousOperation
from django.test import TestCase, override_settings

from workspace.users.models import OIDCIdentity
from workspace.users.services.oidc import WorkspaceOIDCBackend, is_oidc_managed

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

    def test_dedupes_at_max_length_without_infinite_loop(self):
        # A 150-char username that collides must still resolve to a unique
        # name. The naive `f'{base}{suffix}'[:150]` truncates back to `base`,
        # so the dedup loop never terminates and the login request hangs.
        long_name = 'a' * 150
        User.objects.create_user(long_name)
        result = self.backend._generate_username({'preferred_username': long_name})
        self.assertNotEqual(result, long_name)
        self.assertLessEqual(len(result), 150)
        self.assertFalse(User.objects.filter(username=result).exists())


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


@override_settings(**OIDC_OP_SETTINGS)
class OidcIdentitySyncTests(TestCase):
    def setUp(self):
        self.backend = WorkspaceOIDCBackend()

    def test_create_user_creates_identity_marker(self):
        user = self.backend.create_user(
            {'preferred_username': 'jdoe', 'email': 'jdoe@corp.com', 'sub': 'sub-1'})
        self.assertTrue(OIDCIdentity.objects.filter(user=user, sub='sub-1').exists())
        self.assertTrue(is_oidc_managed(user))

    def test_create_user_without_sub_is_not_managed(self):
        user = self.backend.create_user(
            {'preferred_username': 'nosub', 'email': 'nosub@corp.com'})
        self.assertFalse(is_oidc_managed(user))

    def test_update_user_syncs_names_from_claims(self):
        user = User.objects.create_user(
            'existing', email='e@corp.com', first_name='Old', last_name='Name')
        returned = self.backend.update_user(
            user,
            {'email': 'e@corp.com', 'given_name': 'New',
             'family_name': 'Person', 'sub': 'sub-2'},
        )
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'New')
        self.assertEqual(user.last_name, 'Person')
        self.assertEqual(returned, user)

    def test_update_user_does_not_wipe_names_when_claims_absent(self):
        user = User.objects.create_user(
            'keep', email='k@corp.com', first_name='Keep', last_name='Me')
        self.backend.update_user(user, {'email': 'k@corp.com', 'sub': 'sub-3'})
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Keep')
        self.assertEqual(user.last_name, 'Me')

    def test_update_user_links_identity_for_email_matched_user(self):
        user = User.objects.create_user('existing2', email='e2@corp.com')
        self.assertFalse(is_oidc_managed(user))
        self.backend.update_user(user, {'email': 'e2@corp.com', 'sub': 'sub-4'})
        self.assertTrue(is_oidc_managed(user))

    def test_is_oidc_managed_false_for_plain_user(self):
        user = User.objects.create_user('plain', email='p@corp.com')
        self.assertFalse(is_oidc_managed(user))

    def test_is_oidc_managed_false_for_anonymous(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(is_oidc_managed(AnonymousUser()))

    def test_identity_str_includes_sub(self):
        user = User.objects.create_user('struser', email='s@corp.com')
        identity = OIDCIdentity.objects.create(user=user, sub='sub-str')
        self.assertIn('sub-str', str(identity))

    def test_update_user_refuses_changed_sub(self):
        # An existing identity is immutable: a login whose sub disagrees with
        # the stored one (e.g. the email was reused for a different subject)
        # must be refused, not silently accepted.
        user = User.objects.create_user('subchg', email='sc@corp.com')
        OIDCIdentity.objects.create(user=user, sub='sub-A')
        with self.assertRaises(SuspiciousOperation):
            self.backend.update_user(
                user, {'email': 'sc@corp.com', 'sub': 'sub-B'})

    def test_update_user_refuses_sub_owned_by_another_user(self):
        other = User.objects.create_user('owner', email='o@corp.com')
        OIDCIdentity.objects.create(user=other, sub='sub-shared')
        victim = User.objects.create_user('victim', email='v@corp.com')
        with self.assertRaises(SuspiciousOperation):
            self.backend.update_user(
                victim, {'email': 'v@corp.com', 'sub': 'sub-shared'})

    def test_create_user_refuses_sub_owned_by_another_user(self):
        other = User.objects.create_user('owner2', email='o2@corp.com')
        OIDCIdentity.objects.create(user=other, sub='sub-dup')
        with self.assertRaises(SuspiciousOperation):
            self.backend.create_user(
                {'preferred_username': 'newbie', 'email': 'new@corp.com',
                 'sub': 'sub-dup'})
        # The JIT user must be rolled back, not left orphaned.
        self.assertFalse(User.objects.filter(username='newbie').exists())


class OidcPasswordLockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'pwuser', email='pw@corp.com', password='oldpass123')
        self.client.force_login(self.user)

    def test_password_change_allowed_for_plain_user(self):
        resp = self.client.post(
            reverse('user-change-password'),
            data={'current_password': 'oldpass123', 'new_password': 'NewPass!9xyz'},
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

    def test_password_change_blocked_for_oidc_user(self):
        OIDCIdentity.objects.create(user=self.user, sub='sub-pw')
        resp = self.client.post(
            reverse('user-change-password'),
            data={'current_password': 'oldpass123', 'new_password': 'NewPass!9xyz'},
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)


class OidcPasswordUiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'uiuser', email='ui@corp.com', password='localpass1')
        self.client.force_login(self.user)

    def test_password_form_shown_for_plain_user(self):
        resp = self.client.get(reverse('users_ui:settings'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'changePasswordForm()')

    @override_settings(OIDC_PROVIDER_NAME='Keycloak')
    def test_password_form_hidden_for_oidc_user(self):
        OIDCIdentity.objects.create(user=self.user, sub='sub-ui')
        resp = self.client.get(reverse('users_ui:settings'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'changePasswordForm()')
        self.assertContains(resp, 'single sign-on')
