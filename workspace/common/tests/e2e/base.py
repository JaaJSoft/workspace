"""Base class for end-to-end / UI tests powered by Playwright.

Usage:
    from workspace.common.tests.e2e.base import PlaywrightTestCase

    class MyFlowTests(PlaywrightTestCase):
        def test_something(self):
            self.page.goto(f"{self.live_server_url}/some-path")
            ...

E2E tests are **skipped by default**. They only run when the ``E2E``
environment variable is truthy (``E2E=1``). This keeps the per-module CI
matrix (which runs ``manage.py test workspace.<module>``) fast and avoids
requiring Playwright browsers everywhere. A dedicated CI job sets ``E2E=1``
and installs the browsers before running the suite.
"""
from __future__ import annotations

import os
import re
import unittest

# Playwright's sync API (``sync_playwright``) drives the browser over an
# event loop under the hood, which Django's ORM detects as an "async
# context" and uses to raise ``SynchronousOnlyOperation`` on every
# ``Model.objects.create(...)`` call made from the test thread. We are
# not actually running Django in an async runtime, so opt out of that
# safety check — this is the officially documented escape hatch.
# ``setdefault`` avoids clobbering an explicit opt-out from the caller.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402


E2E_ENABLED = os.environ.get("E2E", "").lower() in {"1", "true", "yes", "on"}


@unittest.skipUnless(E2E_ENABLED, "E2E tests are disabled (set E2E=1 to run)")
class PlaywrightTestCase(StaticLiveServerTestCase):
    """Base ``TestCase`` that boots a sync Playwright browser once per class.

    Each test gets a fresh browser ``context`` (and therefore a fresh set of
    cookies) via ``self.context``, plus a convenience ``self.page`` already
    attached to that context.
    """

    # Override in subclasses if you need a different browser (``firefox``, ``webkit``).
    BROWSER_NAME = "chromium"
    HEADLESS = True
    # When True, the test fails if the browser raised any uncaught JS
    # exception (``pageerror``) or any same-origin request failed during
    # the test. Third-party CDN failures and navigation-aborted requests
    # (``net::ERR_ABORTED``, fired e.g. when an in-flight SSE long-poll
    # is cancelled by ``page.goto`` / teardown) are tolerated — they're
    # operational, not regressions in our code.
    #
    # Default is **opt-in** for now: ``base.html`` currently emits
    # uncaught exceptions on every page load when the navbar reads
    # ``$store.notifications.*`` / ``$store.push.*`` before the
    # ``alpine:init`` handler has registered the stores. Flipping this
    # to True today would fail every smoke test on the branch. Once the
    # store-init race is fixed, flip the default and individual smoke
    # tests gain an anti-regression guard for free.
    STRICT_NO_JS_ERRORS = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Import lazily so the module can be imported even when Playwright
        # isn't installed (E2E=0 path).
        from playwright.sync_api import sync_playwright

        cls._playwright = sync_playwright().start()
        browser_type = getattr(cls._playwright, cls.BROWSER_NAME)
        cls.browser = browser_type.launch(headless=cls.HEADLESS)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls._playwright.stop()
            super().tearDownClass()

    # The SSE handler in ``workspace/core/views_sse.py`` is a 600s long-poll
    # that runs in a server worker thread. The thread doesn't observe
    # client disconnect between ``time.sleep(1)`` ticks, so when a test
    # ends its in-flight stream keeps holding a DB connection. On Windows
    # this prevents Django's test runner from deleting the test SQLite
    # file at suite teardown (``PermissionError: file is in use``). No
    # e2e test today drives SSE, so short-circuiting ``/api/v1/stream``
    # with a 204 keeps the page happy without leaking a worker thread.
    # Set to ``False`` for tests that genuinely need SSE.
    STUB_GLOBAL_SSE = True

    def setUp(self):
        super().setUp()
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

        if self.STUB_GLOBAL_SSE:
            self.context.route(
                "**/api/v1/stream**",
                lambda route: route.fulfill(status=204, body=""),
            )

        # Diagnostics: capture in-browser errors + failed requests so a
        # test failure's root cause lands directly in the CI log, without
        # having to pull artifacts.
        self._console_messages: list[str] = []
        self._page_errors: list[str] = []
        self._failed_requests: list[str] = []

        def _on_console(msg):
            if msg.type in {"error", "warning"}:
                self._console_messages.append(f"{msg.type}: {msg.text}")

        self.page.on("console", _on_console)
        self.page.on("pageerror", lambda exc: self._page_errors.append(str(exc)))
        self.page.on(
            "requestfailed",
            lambda req: self._failed_requests.append(
                f"{req.method} {req.url} — {req.failure}"
            ),
        )

        # Make diagnostics fire on *any* test outcome via addCleanup: this
        # runs before tearDown and has reliable access to the current
        # exception info, unlike fiddling with ``self._outcome``.
        self.addCleanup(self._dump_diagnostics)
        # Cleanups run LIFO, so this assertion fires BEFORE the dump,
        # which is what we want: the dump's ``sys.exc_info()`` check then
        # picks up our AssertionError and prints the captured browser
        # state alongside it.
        self.addCleanup(self._assert_no_js_errors)

    def tearDown(self):
        try:
            self.context.close()
        finally:
            super().tearDown()

    def _assert_no_js_errors(self):
        """Fail the test if the browser surfaced uncaught JS exceptions
        or genuine same-origin network failures.

        Catches the bug class that smoke tests miss: an Alpine component
        that throws on init, a ``|json_script`` ID that doesn't exist,
        a misconfigured CSP. ``pageerror`` captures uncaught JS
        exceptions; ``requestfailed`` captures network-level failures
        (DNS, TCP reset, blocked by browser). Note this does *not*
        observe HTTP responses — a 401 / 500 that the UI swallows
        without raising will not be caught here; add a ``response``
        listener if you need that signal.

        Tolerated:

        * Third-party CDN failures (cropper, lucide…) — flake
          independently of the code under test.
        * ``net::ERR_ABORTED`` on any URL — fired when the browser
          cancels an in-flight request because the page navigated or
          tore down (typical for the SSE ``/api/v1/stream`` long-poll
          on logout / teardown). Aborts are intentional, not failures.
        """
        if not self.STRICT_NO_JS_ERRORS:
            return

        own_failed = [
            r for r in self._failed_requests
            if self.live_server_url in r and "ERR_ABORTED" not in r
        ]
        if not self._page_errors and not own_failed:
            return

        parts = ["Browser surfaced errors during the test:"]
        if self._page_errors:
            parts.append("  page errors:")
            parts.extend(f"    - {e}" for e in self._page_errors)
        if own_failed:
            parts.append("  same-origin failed requests:")
            parts.extend(f"    - {r}" for r in own_failed)
        raise AssertionError("\n".join(parts))

    def _dump_diagnostics(self):
        """Print captured browser diagnostics — only when something failed."""
        import sys

        exc_type, exc, _tb = sys.exc_info()
        if exc is None:
            return

        print(f"\n[e2e] ─── diagnostics for {self._testMethodName} ───")
        try:
            print(f"[e2e] current url: {self.page.url}")
        except Exception as e:
            print(f"[e2e] current url: <unavailable: {e}>")
        try:
            print(f"[e2e] current title: {self.page.title()}")
        except Exception as e:
            print(f"[e2e] current title: <unavailable: {e}>")
        for msg in self._console_messages:
            print(f"[e2e] console: {msg}")
        for err in self._page_errors:
            print(f"[e2e] pageerror: {err}")
        for req in self._failed_requests:
            print(f"[e2e] requestfailed: {req}")
        try:
            body = self.page.locator("body").inner_text(timeout=1000)
            # Keep the dump bounded — 2000 chars is enough to see error alerts.
            print(f"[e2e] body text (first 2000 chars):\n{body[:2000]}")
        except Exception as e:
            print(f"[e2e] body text: <unavailable: {e}>")
        print("[e2e] ─── end diagnostics ───")

    # ---- helpers ---------------------------------------------------------

    def create_user(self, username="alice", password="pass12345", *,
                    seen_changelog=True, **extra):
        """Create and return a regular user for use in tests.

        By default the user is marked as having already seen the latest
        ``CHANGELOG.md`` entry, so the auto-open "What's new" modal does
        not race other UI interactions. Pass ``seen_changelog=False`` if
        the test specifically exercises the auto-open behaviour.
        """
        User = get_user_model()
        user = User.objects.create_user(
            username=username,
            password=password,
            email=extra.pop("email", f"{username}@example.com"),
            **extra,
        )
        if seen_changelog:
            from workspace.core.changelog import get_latest_version
            from workspace.users.services.settings import set_setting
            latest = get_latest_version()
            if latest:
                set_setting(
                    user, "core", "changelog_last_seen_version", latest,
                )
        return user

    def login_via_ui(self, username, password, *, wait_for_redirect=True):
        """Log in through the actual login form.

        Use this only when the test is exercising the login page itself;
        for every other authenticated test use ``login_as`` (far faster —
        no form, no network round-trip through the UI).

        When ``wait_for_redirect`` is False the helper returns immediately
        after clicking Submit, so failure-path tests can assert on the
        error alert without racing Playwright's URL watcher.
        """
        self.page.goto(f"{self.live_server_url}/login")
        self.page.locator('input[name="username"]').fill(username)
        self.page.locator('input[name="password"]').fill(password)
        self.page.get_by_role("button", name=re.compile("Sign In", re.I)).click()
        if wait_for_redirect:
            self.page.wait_for_url(lambda url: "/login" not in url)

    def login_as(self, user):
        """Authenticate the current browser context as ``user`` without the UI.

        Creates a session in the test DB via Django's test ``Client`` and
        copies the session cookie into the Playwright ``BrowserContext``.
        Fast (~10 ms) — prefer this over ``login_via_ui`` for every test
        that is not exercising the login page itself.

        Works because ``StaticLiveServerTestCase`` runs the live server
        against the same test database as the test runner, so a session
        row written by the test ``Client`` is visible to the live server.
        """
        from django.conf import settings
        from django.test import Client

        client = Client()
        client.force_login(user)
        cookie = client.cookies[settings.SESSION_COOKIE_NAME]
        self.context.add_cookies([{
            "name": settings.SESSION_COOKIE_NAME,
            "value": cookie.value,
            "url": self.live_server_url,
        }])
