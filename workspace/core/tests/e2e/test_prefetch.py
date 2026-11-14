"""A link to a ``@prefetchable`` page is fetched on hover and the click uses it.

The Speculation Rules are generated on the server, but only the browser checks
them: an invalid rule set is dropped silently, and a rule that matches too
little prefetches nothing. So the check is the browser's own verdict: whether a
speculative request left on hover, and the ``deliveryType`` of the navigation
the click produced.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from workspace.common.tests.e2e.base import PlaywrightTestCase

ADD_LINK = """([href, attributes]) => {
  const link = document.createElement('a');
  link.id = 'probe';
  link.href = href;
  for (const [name, value] of Object.entries(attributes)) link.setAttribute(name, value);
  link.textContent = 'Probe';
  link.style.cssText = 'position:fixed;top:300px;left:600px;z-index:9999;padding:20px;background:#fff';
  document.body.append(link);
}"""

# The navigation the click produced. A click alpine-ajax intercepted only
# pushes a history entry, which leaves this on the page the test started from.
NAVIGATION = """() => {
  const entry = performance.getEntriesByType('navigation')[0];
  return { path: new URL(entry.name).pathname, delivery: entry.deliveryType };
}"""

# Moderate eagerness starts after 200 ms of hover. A prefetch that has not
# started by then never will.
NO_PREFETCH_WINDOW_MS = 1000


def _is_prefetch(request):
    return request.headers.get("sec-purpose", "").startswith("prefetch")


class PrefetchOnHoverTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.login_as(self.create_user())
        self.page.set_viewport_size({"width": 1280, "height": 900})

    def _add_link(self, href, attributes=None):
        self.page.goto(f"{self.live_server_url}/files")
        self.page.wait_for_load_state("networkidle")
        self.page.evaluate(ADD_LINK, [href, attributes or {}])

    def _click(self, href):
        # The test is about what the browser fetched on hover, not about how
        # the click is handled: alpine-ajax leaves a noajax link to the browser.
        self.page.evaluate(
            "document.getElementById('probe').setAttribute('noajax', '')"
        )
        with self.page.expect_navigation():
            self.page.click("#probe")
        navigation = self.page.evaluate(NAVIGATION)
        self.assertEqual(navigation["path"], urlsplit(href).path)
        return navigation["delivery"]

    def assertPrefetched(self, href, attributes=None):
        self._add_link(href, attributes)
        with self.page.expect_event(
            "requestfinished",
            lambda request: _is_prefetch(request) and request.url.endswith(href),
        ):
            self.page.hover("#probe")
        self.assertEqual(self._click(href), "navigational-prefetch")

    def assertNotPrefetched(self, href, attributes=None):
        self._add_link(href, attributes)
        speculative = []
        self.page.on(
            "request",
            lambda request: (
                speculative.append(request.url) if _is_prefetch(request) else None
            ),
        )
        self.page.hover("#probe")
        self.page.wait_for_timeout(NO_PREFETCH_WINDOW_MS)
        self.assertEqual(speculative, [])
        self.assertEqual(self._click(href), "")

    def test_a_prefetchable_page_is_served_from_the_prefetch(self):
        self.assertPrefetched("/notes")

    def test_a_query_string_does_not_stop_the_prefetch(self):
        self.assertPrefetched("/files?view=list")

    def test_a_route_with_a_parameter_is_not_prefetched(self):
        self.assertNotPrefetched("/users/profile/alice")

    def test_a_page_that_did_not_opt_in_is_not_prefetched(self):
        self.assertNotPrefetched("/files/trash")

    def test_a_link_alpine_ajax_intercepts_is_not_prefetched(self):
        self.assertNotPrefetched("/notes", {"x-target.push": "main"})
