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
import unittest

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


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

    def setUp(self):
        super().setUp()
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

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

    def tearDown(self):
        try:
            self.context.close()
        finally:
            super().tearDown()

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

    def create_user(self, username="alice", password="pass12345", **extra):
        """Create and return a regular user for use in tests."""
        User = get_user_model()
        return User.objects.create_user(
            username=username,
            password=password,
            email=extra.pop("email", f"{username}@example.com"),
            **extra,
        )
