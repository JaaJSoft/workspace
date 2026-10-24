import base64
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from knox.models import AuthToken

from workspace.common.dav import auth
from workspace.common.dav.auth import (
    authenticate_basic,
    basic_credentials,
    clear_auth_cache,
)

User = get_user_model()


def _header(value):
    return "Basic " + base64.b64encode(value.encode()).decode()


class BasicCredentialsTests(SimpleTestCase):
    def test_splits_user_and_password(self):
        self.assertEqual(
            basic_credentials(_header("alice:s3:cret")), ("alice", "s3:cret")
        )

    def test_scheme_is_case_insensitive(self):
        header = _header("alice:pw").replace("Basic", "basic")
        self.assertEqual(basic_credentials(header), ("alice", "pw"))

    def test_rejects_missing_or_foreign_headers(self):
        for header in (
            None,
            "",
            "Bearer abc",
            "Basic",
            "Basic !!!",
            _header("nocolon"),
        ):
            with self.subTest(header=header):
                self.assertIsNone(basic_credentials(header))


class AuthenticateBasicTests(TestCase):
    def setUp(self):
        # The cache is module-global and keyed on credentials only: without a
        # reset, a user cached by one test leaks into the next.
        clear_auth_cache()
        self.user = User.objects.create_user(
            username="davdc", email="dc@test.com", password="secret123"
        )

    def tearDown(self):
        clear_auth_cache()

    def test_valid_password(self):
        self.assertEqual(authenticate_basic("davdc", "secret123"), self.user)

    def test_wrong_password_and_unknown_user(self):
        self.assertIsNone(authenticate_basic("davdc", "wrong"))
        self.assertIsNone(authenticate_basic("nobody", "secret123"))

    def test_inactive_user(self):
        self.user.is_active = False
        self.user.save()
        self.assertIsNone(authenticate_basic("davdc", "secret123"))

    def test_result_is_cached(self):
        """A second request with the same credentials must not re-run the
        (expensive) authentication backend within the cache TTL."""
        self.assertEqual(authenticate_basic("davdc", "secret123"), self.user)
        with mock.patch("workspace.common.dav.auth.authenticate") as backend:
            self.assertEqual(authenticate_basic("davdc", "secret123"), self.user)
        backend.assert_not_called()

    def test_expired_cache_entries_are_evicted(self):
        """The cache lives as long as the worker: an entry past its TTL must
        not stay in memory once another login is cached."""
        other = User.objects.create_user(username="davdc2", password="secret456")
        with mock.patch("workspace.common.dav.auth.time.monotonic", return_value=0):
            authenticate_basic("davdc", "secret123")
        later = auth.AUTH_TTL + 1
        with mock.patch("workspace.common.dav.auth.time.monotonic", return_value=later):
            authenticate_basic("davdc2", "secret456")
        self.assertEqual([user for user, _ in auth._auth_cache.values()], [other])

    def test_wrong_password_not_served_from_cache(self):
        """Caching a success must not let a wrong password through."""
        self.assertIsNotNone(authenticate_basic("davdc", "secret123"))
        self.assertIsNone(authenticate_basic("davdc", "wrong"))

    def test_api_token_as_password(self):
        _, token = AuthToken.objects.create(self.user)
        self.assertEqual(authenticate_basic("davdc", token), self.user)

    def test_api_token_for_user_without_usable_password(self):
        # The OIDC scenario: no usable local password, a token is the only
        # credential such an account can present over Basic auth.
        sso_user = User.objects.create_user(username="ssodav", email="sso@test.com")
        self.assertFalse(sso_user.has_usable_password())
        _, token = AuthToken.objects.create(sso_user)
        self.assertEqual(authenticate_basic("ssodav", token), sso_user)

    def test_api_token_with_wrong_username(self):
        other = User.objects.create_user(
            username="otherdav", email="other@test.com", password="pw12345"
        )
        _, token = AuthToken.objects.create(other)
        self.assertIsNone(authenticate_basic("davdc", token))

    def test_expired_api_token(self):
        _, token = AuthToken.objects.create(self.user, timedelta(seconds=-1))
        self.assertIsNone(authenticate_basic("davdc", token))

    def test_revoked_api_token(self):
        instance, token = AuthToken.objects.create(self.user)
        instance.delete()
        self.assertIsNone(authenticate_basic("davdc", token))

    def test_api_token_inactive_user(self):
        _, token = AuthToken.objects.create(self.user)
        self.user.is_active = False
        self.user.save()
        self.assertIsNone(authenticate_basic("davdc", token))
