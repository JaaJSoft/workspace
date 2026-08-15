from django.conf import settings as dj_settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import SuspiciousOperation
from django.test import TestCase, override_settings
from django.urls import reverse

from workspace.users.models import OIDCIdentity
from workspace.users.services.oidc import WorkspaceOIDCBackend, is_oidc_managed

User = get_user_model()

# Minimal OP settings so OIDCAuthenticationBackend.__init__ (which reads these
# without defaults) does not raise when we instantiate the backend in tests.
OIDC_OP_SETTINGS = dict(
    OIDC_RP_CLIENT_ID="client",
    OIDC_RP_CLIENT_SECRET="secret",
    OIDC_OP_AUTHORIZATION_ENDPOINT="https://op.example/authorize",
    OIDC_OP_TOKEN_ENDPOINT="https://op.example/token",
    OIDC_OP_USER_ENDPOINT="https://op.example/userinfo",
    OIDC_OP_JWKS_ENDPOINT="https://op.example/jwks",
)


@override_settings(**OIDC_OP_SETTINGS)
class VerifyClaimsTests(TestCase):
    def setUp(self):
        self.backend = WorkspaceOIDCBackend()

    @staticmethod
    def claims(**overrides):
        """Claims of a login the IdP considers valid, minus what a test changes."""
        return {"sub": "subject-1", "email": "jane@corp.com"} | overrides

    def test_passes_for_basic_email(self):
        self.assertTrue(self.backend.verify_claims(self.claims()))

    def test_rejects_when_no_email(self):
        self.assertFalse(self.backend.verify_claims({"sub": "x"}))

    def test_rejects_when_no_subject(self):
        # Without a subject the account would be provisioned unlinked, so any
        # later subject could claim it through the email match.
        self.assertFalse(self.backend.verify_claims({"email": "jane@corp.com"}))
        self.assertFalse(self.backend.verify_claims(self.claims(sub="")))
        self.assertFalse(self.backend.verify_claims(self.claims(sub="   ")))
        self.assertFalse(self.backend.verify_claims(self.claims(sub=12345)))

    @override_settings(OIDC_REQUIRE_EMAIL_VERIFIED=True)
    def test_requires_email_verified_when_enabled(self):
        self.assertFalse(self.backend.verify_claims(self.claims()))
        self.assertFalse(self.backend.verify_claims(self.claims(email_verified=False)))
        self.assertTrue(self.backend.verify_claims(self.claims(email_verified=True)))
        self.assertTrue(self.backend.verify_claims(self.claims(email_verified="true")))

    def test_email_verified_not_imposed_by_default(self):
        self.assertTrue(self.backend.verify_claims(self.claims(email_verified=False)))

    @override_settings(OIDC_ALLOWED_DOMAINS=["corp.com"])
    def test_allowlist_rejects_outside_domain(self):
        self.assertFalse(self.backend.verify_claims(self.claims(email="a@evil.com")))
        self.assertTrue(self.backend.verify_claims(self.claims(email="a@corp.com")))

    def test_allowlist_empty_allows_any_domain(self):
        self.assertTrue(self.backend.verify_claims(self.claims(email="a@anything.io")))

    def test_rejects_empty_email(self):
        # 'email' key present but empty: super() passes (key present), our own
        # guard rejects it.
        self.assertFalse(self.backend.verify_claims(self.claims(email="")))

    @override_settings(OIDC_ALLOWED_DOMAINS=["corp.com"])
    def test_allowlist_handles_non_string_email(self):
        # A non-conforming IdP returning a non-string email must be refused
        # cleanly, not raise a 500.
        self.assertFalse(self.backend.verify_claims(self.claims(email=12345)))


@override_settings(**OIDC_OP_SETTINGS)
class GenerateUsernameTests(TestCase):
    def setUp(self):
        self.backend = WorkspaceOIDCBackend()

    def test_uses_preferred_username_by_default(self):
        u = self.backend._generate_username(
            {"preferred_username": "jdoe", "email": "jdoe@corp.com"}
        )
        self.assertEqual(u, "jdoe")

    @override_settings(OIDC_USERNAME_CLAIM="sub")
    def test_uses_configured_claim(self):
        u = self.backend._generate_username(
            {"sub": "xyz", "preferred_username": "ignored", "email": "a@corp.com"}
        )
        self.assertEqual(u, "xyz")

    def test_falls_back_to_email_localpart(self):
        u = self.backend._generate_username({"email": "jane@corp.com"})
        self.assertEqual(u, "jane")

    def test_falls_back_to_sub(self):
        u = self.backend._generate_username({"sub": "sub-123"})
        self.assertEqual(u, "sub-123")

    def test_sanitizes_disallowed_chars(self):
        u = self.backend._generate_username({"preferred_username": "jean dupont!"})
        self.assertEqual(u, "jeandupont")

    def test_dedupes_on_collision(self):
        User.objects.create_user("jdoe")
        u = self.backend._generate_username({"preferred_username": "jdoe"})
        self.assertEqual(u, "jdoe2")

    def test_dedupes_at_max_length_without_infinite_loop(self):
        # A 150-char username that collides must still resolve to a unique
        # name. The naive `f'{base}{suffix}'[:150]` truncates back to `base`,
        # so the dedup loop never terminates and the login request hangs.
        long_name = "a" * 150
        User.objects.create_user(long_name)
        result = self.backend._generate_username({"preferred_username": long_name})
        self.assertNotEqual(result, long_name)
        self.assertLessEqual(len(result), 150)
        self.assertFalse(User.objects.filter(username=result).exists())


@override_settings(**OIDC_OP_SETTINGS)
class CreateUserTests(TestCase):
    def setUp(self):
        self.backend = WorkspaceOIDCBackend()

    def test_creates_user_with_readable_username_and_profile(self):
        claims = {
            "sub": "subject-jdoe",
            "preferred_username": "jdoe",
            "email": "jdoe@corp.com",
            "given_name": "John",
            "family_name": "Doe",
        }
        user = self.backend.create_user(claims)
        self.assertEqual(user.username, "jdoe")  # readable, not a hash
        self.assertEqual(user.email, "jdoe@corp.com")
        self.assertEqual(user.first_name, "John")
        self.assertEqual(user.last_name, "Doe")


class OidcSettingsWiringTests(TestCase):
    def test_oidc_disabled_by_default(self):
        self.assertFalse(dj_settings.OIDC_ENABLED)

    def test_oidc_backend_absent_when_disabled(self):
        # Critical: an unconfigured OIDC backend would raise ImproperlyConfigured
        # on every local login. It must NOT be in the list when disabled.
        self.assertNotIn(
            "workspace.users.services.oidc.WorkspaceOIDCBackend",
            dj_settings.AUTHENTICATION_BACKENDS,
        )
        self.assertIn(
            "django.contrib.auth.backends.ModelBackend",
            dj_settings.AUTHENTICATION_BACKENDS,
        )

    def test_oidc_app_installed(self):
        self.assertIn("mozilla_django_oidc", dj_settings.INSTALLED_APPS)

    def test_secure_and_required_defaults(self):
        self.assertEqual(dj_settings.OIDC_RP_SIGN_ALGO, "RS256")
        self.assertIn("profile", dj_settings.OIDC_RP_SCOPES)
        self.assertFalse(dj_settings.OIDC_REQUIRE_EMAIL_VERIFIED)
        self.assertEqual(dj_settings.OIDC_USERNAME_CLAIM, "preferred_username")


class OidcRoutesTests(TestCase):
    """The routes are ours, not mozilla-django-oidc's slash-suffixed URLconf.

    APPEND_SLASH is False, so a trailing slash is a 404 rather than a redirect,
    and the callback path is what admins register at the IdP - it cannot drift.
    """

    def test_routes_have_no_trailing_slash(self):
        self.assertEqual(reverse("oidc_authentication_init"), "/oidc/authenticate")
        self.assertEqual(reverse("oidc_authentication_callback"), "/oidc/callback")
        self.assertEqual(reverse("oidc_logout"), "/oidc/logout")

    def test_trailing_slash_is_not_routed(self):
        self.assertEqual(self.client.get("/oidc/callback/").status_code, 404)


class LoginPageOidcButtonTests(TestCase):
    def test_button_hidden_when_disabled(self):
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "oidc-login-button")
        # Local username/password form is still rendered.
        self.assertContains(resp, 'name="username"')

    @override_settings(OIDC_ENABLED=True, OIDC_PROVIDER_NAME="Keycloak")
    def test_button_shown_when_enabled(self):
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "oidc-login-button")
        self.assertContains(resp, "Sign in with Keycloak")
        self.assertContains(resp, reverse("oidc_authentication_init"))


@override_settings(**OIDC_OP_SETTINGS)
class OidcIdentitySyncTests(TestCase):
    def setUp(self):
        self.backend = WorkspaceOIDCBackend()

    def test_create_user_creates_identity_marker(self):
        user = self.backend.create_user(
            {"preferred_username": "jdoe", "email": "jdoe@corp.com", "sub": "sub-1"}
        )
        self.assertTrue(OIDCIdentity.objects.filter(user=user, sub="sub-1").exists())
        self.assertTrue(is_oidc_managed(user))

    def test_create_user_without_sub_is_refused(self):
        # An unlinked account is what the subject binding exists to prevent:
        # it would not be IdP-managed and any subject could later claim it.
        with self.assertRaises(SuspiciousOperation):
            self.backend.create_user(
                {"preferred_username": "nosub", "email": "nosub@corp.com"}
            )
        self.assertFalse(User.objects.filter(username="nosub").exists())

    def test_update_user_syncs_names_from_claims(self):
        user = User.objects.create_user(
            "existing", email="e@corp.com", first_name="Old", last_name="Name"
        )
        returned = self.backend.update_user(
            user,
            {
                "email": "e@corp.com",
                "given_name": "New",
                "family_name": "Person",
                "sub": "sub-2",
            },
        )
        user.refresh_from_db()
        self.assertEqual(user.first_name, "New")
        self.assertEqual(user.last_name, "Person")
        self.assertEqual(returned, user)

    def test_update_user_does_not_wipe_names_when_claims_absent(self):
        user = User.objects.create_user(
            "keep", email="k@corp.com", first_name="Keep", last_name="Me"
        )
        self.backend.update_user(user, {"email": "k@corp.com", "sub": "sub-3"})
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Keep")
        self.assertEqual(user.last_name, "Me")

    def test_update_user_links_identity_for_email_matched_user(self):
        user = User.objects.create_user("existing2", email="e2@corp.com")
        self.assertFalse(is_oidc_managed(user))
        self.backend.update_user(user, {"email": "e2@corp.com", "sub": "sub-4"})
        self.assertTrue(is_oidc_managed(user))

    def test_linked_subject_wins_over_the_email_match(self):
        # The address changed at the IdP. Matching on email alone would miss
        # the account, provision a second one, and then refuse the login on the
        # already-bound subject - locking the user out of their own account.
        user = User.objects.create_user("moved", email="old@corp.com")
        OIDCIdentity.objects.create(user=user, sub="sub-moved")

        matched = self.backend.filter_users_by_claims(
            {"sub": "sub-moved", "email": "new@corp.com"}
        )

        self.assertEqual([u.pk for u in matched], [user.pk])

    def test_unknown_subject_still_matches_on_email(self):
        user = User.objects.create_user("bymail", email="bymail@corp.com")

        matched = self.backend.filter_users_by_claims(
            {"sub": "sub-never-seen", "email": "bymail@corp.com"}
        )

        self.assertEqual([u.pk for u in matched], [user.pk])

    def test_update_user_syncs_a_changed_email(self):
        user = User.objects.create_user("moved2", email="old2@corp.com")
        OIDCIdentity.objects.create(user=user, sub="sub-moved2")

        self.backend.update_user(user, {"sub": "sub-moved2", "email": "new2@corp.com"})

        user.refresh_from_db()
        self.assertEqual(user.email, "new2@corp.com")

    def test_update_user_without_sub_is_refused(self):
        user = User.objects.create_user("nosub2", email="nosub2@corp.com")
        with self.assertRaises(SuspiciousOperation):
            self.backend.update_user(user, {"email": "nosub2@corp.com"})
        self.assertFalse(is_oidc_managed(user))

    def test_is_oidc_managed_false_for_plain_user(self):
        user = User.objects.create_user("plain", email="p@corp.com")
        self.assertFalse(is_oidc_managed(user))

    def test_is_oidc_managed_false_for_anonymous(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(is_oidc_managed(AnonymousUser()))

    def test_identity_str_includes_sub(self):
        user = User.objects.create_user("struser", email="s@corp.com")
        identity = OIDCIdentity.objects.create(user=user, sub="sub-str")
        self.assertIn("sub-str", str(identity))

    def test_update_user_refuses_changed_sub(self):
        # An existing identity is immutable: a login whose sub disagrees with
        # the stored one (e.g. the email was reused for a different subject)
        # must be refused, not silently accepted.
        user = User.objects.create_user("subchg", email="sc@corp.com")
        OIDCIdentity.objects.create(user=user, sub="sub-A")
        with self.assertRaises(SuspiciousOperation):
            self.backend.update_user(user, {"email": "sc@corp.com", "sub": "sub-B"})

    def test_update_user_refuses_sub_owned_by_another_user(self):
        other = User.objects.create_user("owner", email="o@corp.com")
        OIDCIdentity.objects.create(user=other, sub="sub-shared")
        victim = User.objects.create_user("victim", email="v@corp.com")
        with self.assertRaises(SuspiciousOperation):
            self.backend.update_user(
                victim, {"email": "v@corp.com", "sub": "sub-shared"}
            )

    def test_create_user_refuses_sub_owned_by_another_user(self):
        other = User.objects.create_user("owner2", email="o2@corp.com")
        OIDCIdentity.objects.create(user=other, sub="sub-dup")
        with self.assertRaises(SuspiciousOperation):
            self.backend.create_user(
                {
                    "preferred_username": "newbie",
                    "email": "new@corp.com",
                    "sub": "sub-dup",
                }
            )
        # The JIT user must be rolled back, not left orphaned.
        self.assertFalse(User.objects.filter(username="newbie").exists())


class OidcPasswordLockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "pwuser", email="pw@corp.com", password="oldpass123"
        )
        self.client.force_login(self.user)

    def test_password_change_allowed_for_plain_user(self):
        resp = self.client.post(
            reverse("user-change-password"),
            data={"current_password": "oldpass123", "new_password": "NewPass!9xyz"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_password_change_blocked_for_oidc_user(self):
        OIDCIdentity.objects.create(user=self.user, sub="sub-pw")
        resp = self.client.post(
            reverse("user-change-password"),
            data={"current_password": "oldpass123", "new_password": "NewPass!9xyz"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)


class OidcPasswordUiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "uiuser", email="ui@corp.com", password="localpass1"
        )
        self.client.force_login(self.user)

    def tearDown(self):
        # The settings page caches usage stats per user id; leaving them behind
        # leaks into any later test that asserts on those numbers.
        cache.clear()

    def test_password_form_shown_for_plain_user(self):
        resp = self.client.get(reverse("users_ui:settings"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "changePasswordForm()")

    @override_settings(OIDC_PROVIDER_NAME="Keycloak")
    def test_password_form_hidden_for_oidc_user(self):
        OIDCIdentity.objects.create(user=self.user, sub="sub-ui")
        resp = self.client.get(reverse("users_ui:settings"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "changePasswordForm()")
        self.assertContains(resp, "single sign-on")
