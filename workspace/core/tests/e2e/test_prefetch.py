"""A link to a ``@prefetchable`` page is fetched on hover and the click uses it.

The Speculation Rules are generated on the server, but only the browser checks
them: an invalid rule set is dropped silently, and a rule that matches too
little prefetches nothing. So the check is the browser's own verdict - the
``deliveryType`` of the navigation the click produced.
"""

from __future__ import annotations

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

# Clones the link so no alpine-ajax listener can intercept the click: the test
# is about what the browser fetched on hover, not about how the click is handled.
FOLLOW_AS_PLAIN_LINK = """() => {
  const link = document.getElementById('probe');
  link.replaceWith(link.cloneNode(true));
}"""


class PrefetchOnHoverTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.login_as(self.create_user())
        self.page.set_viewport_size({"width": 1280, "height": 900})

    def _delivery_after_hover(self, href, attributes=None):
        self.page.goto(f"{self.live_server_url}/files")
        self.page.wait_for_load_state("networkidle")
        self.page.evaluate(ADD_LINK, [href, attributes or {}])
        self.page.hover("#probe")
        # Moderate eagerness starts after 200 ms of hover; leave the fetch time.
        self.page.wait_for_timeout(1000)
        self.page.evaluate(FOLLOW_AS_PLAIN_LINK)
        with self.page.expect_navigation():
            self.page.click("#probe")
        return self.page.evaluate(
            "performance.getEntriesByType('navigation')[0].deliveryType"
        )

    def test_a_prefetchable_page_is_served_from_the_prefetch(self):
        self.assertEqual(self._delivery_after_hover("/notes"), "navigational-prefetch")

    def test_a_query_string_does_not_stop_the_prefetch(self):
        self.assertEqual(
            self._delivery_after_hover("/files?view=list"), "navigational-prefetch"
        )

    def test_a_route_with_a_parameter_is_not_prefetched(self):
        self.assertEqual(self._delivery_after_hover("/users/profile/alice"), "")

    def test_a_page_that_did_not_opt_in_is_not_prefetched(self):
        self.assertEqual(self._delivery_after_hover("/files/trash"), "")

    def test_a_link_alpine_ajax_intercepts_is_not_prefetched(self):
        self.assertEqual(
            self._delivery_after_hover("/notes", {"x-target.push": "main"}), ""
        )
