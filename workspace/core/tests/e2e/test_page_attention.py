"""E2E: requests a page sends while nobody is in front of it are not activity.

A tab left open keeps refreshing on its own (SSE-driven swaps). If those
requests counted as presence activity, the push task would keep deferring
web push to the user's phone as if they were at the PC.
"""

from __future__ import annotations

from django.core.cache import cache

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.users.services.presence import is_active


class PageAttentionPresenceTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="attention")
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")
        self.page.wait_for_load_state("networkidle")

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _fetch_from_page(self):
        self.page.evaluate(
            "() => fetch('/api/v1/notifications?limit=1').then((r) => r.status)"
        )

    def _hide_page(self):
        self.page.evaluate(
            "() => Object.defineProperty(document, 'visibilityState',"
            " { configurable: true, get: () => 'hidden' })"
        )

    def test_request_from_hidden_page_does_not_count_as_activity(self):
        self._hide_page()
        cache.clear()

        self._fetch_from_page()

        self.assertFalse(is_active(self.user.id))

    def test_request_from_attended_page_counts_as_activity(self):
        cache.clear()

        self._fetch_from_page()

        self.assertTrue(is_active(self.user.id))

    def test_hidden_page_flags_its_requests(self):
        self._hide_page()

        with self.page.expect_request("**/api/v1/notifications*") as info:
            self._fetch_from_page()

        self.assertEqual(info.value.headers.get("x-page-unattended"), "1")
