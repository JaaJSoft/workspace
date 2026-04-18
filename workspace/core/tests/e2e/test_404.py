"""E2E test: requests to an unknown URL return HTTP 404.

Guards against a URL-routing regression (e.g., a catch-all that
accidentally matches too much, or a misconfigured middleware that
converts every miss into a 500).
"""
from __future__ import annotations

from workspace.common.tests.e2e.base import PlaywrightTestCase


class NotFoundTests(PlaywrightTestCase):
    """Hitting an unknown URL yields a 404 response."""

    def test_unknown_url_returns_404(self):
        response = self.page.goto(
            f"{self.live_server_url}/this-path-really-should-not-exist",
        )
        assert response is not None, "Playwright returned no response"
        assert response.status == 404, (
            f"expected 404, got {response.status} at {self.page.url}"
        )
