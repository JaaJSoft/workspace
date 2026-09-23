import re
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import URLResolver, get_resolver
from knox.models import AuthToken
from rest_framework.views import APIView

from workspace.core.module_guard import ModuleVisible
from workspace.core.services.module_visibility import owning_module

User = get_user_model()


# The page masks its CSRF token afresh on every render: two renders of the very
# same page differ there, and in the two headers computed from the body.
_CSRF_VALUE = re.compile(rb'(name="csrfmiddlewaretoken" value=")[^"]+"')
_BODY_DERIVED_HEADERS = {"Content-Length", "ETag"}


def _snapshot(response, path):
    """Everything a caller could compare between two answers.

    The 404 page echoes the path it was asked for (the login link carries it
    as ``next``), and the two paths compared differ by construction.
    """
    body = _CSRF_VALUE.sub(rb'\1"', response.content)
    body = body.replace(path.encode(), b"<path>")
    headers = sorted(
        (name, value)
        for name, value in response.headers.items()
        if name not in _BODY_DERIVED_HEADERS
    )
    return response.status_code, body, headers


class _GuardTestCase(TestCase):
    def setUp(self):
        self.regular = User.objects.create_user(username="regular", password="pw")
        self.staff = User.objects.create_user(
            username="staff", password="pw", is_staff=True
        )

    def tearDown(self):
        cache.clear()

    def assertAnswersLikeAnAbsentPath(
        self, hidden_path, absent_path, method="get", client=None, **headers
    ):
        send = getattr(client or self.client, method)
        hidden = send(hidden_path, **headers)
        absent = send(absent_path, **headers)
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(_snapshot(hidden, hidden_path), _snapshot(absent, absent_path))


@override_settings(PREVIEW_VISIBILITY="staff")
class PageGuardTests(_GuardTestCase):
    def test_a_hidden_page_answers_exactly_like_an_absent_one(self):
        self.client.force_login(self.regular)
        self.assertAnswersLikeAnAbsentPath("/vault", "/vault/does-not-exist")

    def test_a_post_without_a_csrf_token_cannot_tell_them_apart_either(self):
        """The CSRF check would answer 403 before any view ran - and an
        unmatched URL never reaches it, so it answers 404."""
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.regular)
        self.assertAnswersLikeAnAbsentPath(
            "/vault/onboarding", "/vault/does-not-exist", method="post", client=client
        )

    def test_every_page_of_a_preview_module_is_refused(self):
        self.client.force_login(self.regular)
        for path in (
            "/vault",
            "/vault/onboarding",
            f"/vault/{uuid.uuid4()}",
            "/imports",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_the_audience_still_reaches_the_page(self):
        self.client.force_login(self.staff)
        self.assertNotEqual(self.client.get("/vault").status_code, 404)

    def test_an_anonymous_visitor_is_still_sent_to_the_login_page(self):
        response = self.client.get("/vault")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    @override_settings(PREVIEW_VISIBILITY="none")
    def test_a_module_out_of_preview_is_never_refused(self):
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get("/files").status_code, 200)


@override_settings(PREVIEW_VISIBILITY="staff")
class ApiGuardTests(_GuardTestCase):
    def test_a_hidden_endpoint_answers_exactly_like_an_absent_one(self):
        self.client.force_login(self.regular)
        self.assertAnswersLikeAnAbsentPath(
            "/api/v1/vault/vaults", "/api/v1/vault/does-not-exist"
        )

    def test_a_token_client_cannot_tell_them_apart_either(self):
        """DRF writes the token's user onto the Django request. Rendered as it
        stands, the 404 page would greet that user by name, where an unmatched
        URL - which never reaches DRF - renders for an anonymous visitor."""
        _, token = AuthToken.objects.create(self.regular)
        self.assertAnswersLikeAnAbsentPath(
            "/api/v1/vault/vaults",
            "/api/v1/vault/does-not-exist",
            HTTP_AUTHORIZATION=f"Token {token}",
        )

    def test_another_preview_module_is_refused_too(self):
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get("/api/v1/imports/jobs").status_code, 404)

    def test_a_session_write_without_a_csrf_token_cannot_tell_them_apart(self):
        """DRF's session authentication enforces CSRF before any permission
        runs, and answers 403 where an unmatched URL answers 404."""
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.regular)
        self.assertAnswersLikeAnAbsentPath(
            "/api/v1/vault/account/init",
            "/api/v1/vault/does-not-exist",
            method="post",
            client=client,
        )

    def test_a_write_is_refused_before_it_is_read(self):
        self.client.force_login(self.regular)
        init = self.client.post(
            "/api/v1/vault/account/init", {}, content_type="application/json"
        )
        self.assertEqual(init.status_code, 404)

    def test_the_audience_reaches_the_endpoint(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/api/v1/vault/vaults").status_code, 200)

    def test_an_anonymous_caller_still_gets_a_403(self):
        self.assertEqual(self.client.get("/api/v1/vault/vaults").status_code, 403)

    def test_each_audience_level_admits_exactly_its_users(self):
        users = {
            "regular": self.regular,
            "staff": self.staff,
            "root": User.objects.create_superuser(username="root", password="pw"),
        }
        admitted = {
            "all": {"regular", "staff", "root"},
            "staff": {"staff", "root"},
            "admin": {"root"},
            "none": set(),
        }
        for level, names in admitted.items():
            for name, user in users.items():
                with (
                    self.subTest(level=level, user=name),
                    override_settings(PREVIEW_VISIBILITY=level),
                ):
                    self.client.force_login(user)
                    response = self.client.get("/api/v1/vault/vaults")
                    self.assertEqual(
                        response.status_code, 200 if name in names else 404
                    )


class GuardCoverageTests(SimpleTestCase):
    """The guard knows a view's module by where the view's code lives. Two
    ways out of it: a route whose view is defined elsewhere (a bare
    ``TemplateView``, a decorator that drops ``__module__``), and an API view
    whose permissions replace the defaults, ModuleVisible with them. Routes
    are attributed here by the URLconf that declares them instead, which
    neither can fool."""

    def _preview_routes(self):
        found = []

        def walk(patterns, owner):
            for pattern in patterns:
                if isinstance(pattern, URLResolver):
                    # A dotted path, a module, or a bare list of patterns
                    # (the admin's), which inherits its parent's owner.
                    urlconf = pattern.urlconf_name
                    name = urlconf if isinstance(urlconf, str) else ""
                    name = getattr(urlconf, "__name__", name)
                    walk(pattern.url_patterns, owning_module(name) or owner)
                elif owner is not None and owner.preview:
                    found.append((str(pattern.pattern), owner, pattern.callback))

        walk(get_resolver().url_patterns, None)
        return found

    def test_the_walk_reaches_the_preview_routes(self):
        # A walk that finds nothing would let every route through.
        routes = {(owner.slug, route) for route, owner, _ in self._preview_routes()}
        self.assertIn(("vault", ""), routes)
        self.assertIn(("vault", "api/v1/vault/vaults"), routes)

    def test_every_preview_route_is_attributed_to_its_module(self):
        for route, owner, callback in self._preview_routes():
            with self.subTest(route=route):
                self.assertIs(owning_module(callback.__module__), owner)

    def test_no_preview_api_view_drops_the_module_guard(self):
        for route, _, callback in self._preview_routes():
            view_class = getattr(callback, "cls", None)
            if not (isinstance(view_class, type) and issubclass(view_class, APIView)):
                continue
            with self.subTest(route=route):
                permissions = view_class().get_permissions()
                self.assertTrue(any(isinstance(p, ModuleVisible) for p in permissions))

    def test_the_middleware_refuses_before_the_csrf_check(self):
        middleware = settings.MIDDLEWARE
        self.assertLess(
            middleware.index("workspace.core.module_guard.PreviewModuleMiddleware"),
            middleware.index("django.middleware.csrf.CsrfViewMiddleware"),
        )
