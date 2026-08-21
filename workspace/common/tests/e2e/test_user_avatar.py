"""E2E: <user-avatar> lifecycle in a real browser.

The element is only observable once the browser has upgraded it, so neither
Django's test client nor the node:vm unit tests can see any of this. Two
lifecycle bugs are pinned here, both reported against the first revision of
the element:

1. A re-render (any observed attribute changing) rebuilds the face and the
   dot, which throws away the classes the presence patch had applied. The
   patch memoises what it last wrote, so it would compare against a status
   that still matched, skip the write, and leave the fresh nodes colourless.

2. The hover card opens on a delay. An avatar removed from the DOM while
   that timer was in flight left the timer to fire against a detached
   element, building a popover and appending it to <body> with nothing left
   on screen able to dismiss it.

The `name` attribute is pinned here for the same reason: whether the hover
card covers the name, and whether the presence dot still lands on the picture
once the host is a row, are facts about layout that only a browser knows.
"""

from __future__ import annotations

from workspace.common.tests.e2e.base import PlaywrightTestCase


class UserAvatarLifecycleTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="avatar-user")
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/files")
        self.page.wait_for_load_state("networkidle")

    def _mount(self, attrs=""):
        """Put a single avatar on the page and wait for it to upgrade."""
        self.page.evaluate(
            """
            ([uid, attrs]) => {
              const host = document.createElement('div');
              host.id = 'probe-host';
              host.innerHTML =
                `<user-avatar id="probe" user-id="${uid}" username="Probe" size="md" ${attrs}></user-avatar>`;
              document.body.appendChild(host);
            }
            """,
            [self.user.id, attrs],
        )
        self.page.wait_for_selector("#probe > span", state="attached")

    def test_presence_colour_survives_a_re_render(self):
        self._mount("presence")

        # Drive the store to a status whose colour differs from the static
        # default, so a dropped patch is visible rather than coincidental.
        self.page.evaluate(
            "(uid) => Alpine.store('presence').setLocalStatus(uid, 'online')",
            self.user.id,
        )
        self.page.wait_for_timeout(300)

        def colours():
            return self.page.evaluate(
                """
                () => {
                  const el = document.getElementById('probe');
                  const dot = el.querySelector('[data-presence-dot]');
                  return {
                    ring: [...el.firstElementChild.classList].filter(c => c.startsWith('ring-')),
                    dot: dot ? [...dot.classList].filter(c => c.startsWith('bg-')) : [],
                  };
                }
                """
            )

        before = colours()
        self.assertIn("ring-success", before["ring"])
        self.assertIn("bg-success", before["dot"])

        # Any observed attribute forces a full re-render of the children.
        self.page.evaluate(
            "() => document.getElementById('probe').setAttribute('size', 'lg')"
        )
        self.page.wait_for_timeout(300)

        after = colours()
        self.assertIn(
            "ring-success", after["ring"], "the ring lost its colour on re-render"
        )
        self.assertIn(
            "bg-success", after["dot"], "the presence dot lost its colour on re-render"
        )

    def test_removing_an_avatar_mid_hover_leaves_no_orphan_card(self):
        self._mount("card")

        # Start the show timer, then rip the element out before it fires.
        self.page.hover("#probe")
        self.page.evaluate("() => document.getElementById('probe-host').remove()")

        # Comfortably past the popover's delay.
        self.page.wait_for_timeout(2000)

        orphans = self.page.evaluate(
            "() => document.querySelectorAll('.user-card-popover').length"
        )
        self.assertEqual(orphans, 0, "a user card was left floating over the page")


class UserAvatarNameTests(PlaywrightTestCase):
    """`name` renders the name inside the element, so `card` covers it."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="avatar-user")
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/files")
        self.page.wait_for_load_state("networkidle")

    def _mount(self, attrs=""):
        """Mount a single named avatar, replacing any previous probe."""
        self.page.evaluate(
            """
            ([uid, attrs]) => {
              document.getElementById('probe-host')?.remove();
              const host = document.createElement('div');
              host.id = 'probe-host';
              // Narrow on purpose: a name that does not truncate would push
              // the row wider than its container instead of ellipsising.
              host.style.width = '120px';
              host.innerHTML =
                `<user-avatar id="probe" user-id="${uid}" username="Probe" size="md" ${attrs}></user-avatar>`;
              document.body.appendChild(host);
            }
            """,
            [self.user.id, attrs],
        )
        self.page.wait_for_selector("#probe > span", state="attached")

    def test_the_name_is_rendered_inside_the_element(self):
        self._mount('name display-name="Ada Lovelace"')

        self.assertEqual(
            self.page.text_content("#probe [data-user-name]"),
            "Ada Lovelace",
            "display-name did not reach the name node",
        )

        # Without display-name the username is what shows.
        self._mount("name")
        self.assertEqual(self.page.text_content("#probe [data-user-name]"), "Probe")

    def test_hovering_the_name_opens_the_card(self):
        self._mount('name card display-name="Ada Lovelace"')

        # The point of the attribute: the name is INSIDE the hover target, so
        # pointing at it is pointing at the avatar.
        self.page.hover("#probe >> text=Ada Lovelace")
        self.page.wait_for_selector(".user-card-popover", state="visible", timeout=4000)

        self.assertEqual(
            self.page.evaluate(
                "() => document.querySelectorAll('.user-card-popover').length"
            ),
            1,
        )

    def test_the_name_links_to_the_profile_when_given_an_href(self):
        self._mount('name href="/users/profile/avatar-user"')

        self.assertEqual(
            self.page.get_attribute("#probe a", "href"), "/users/profile/avatar-user"
        )
        # No href, no link: most rows sit inside something already clickable.
        self._mount("name")
        self.assertEqual(self.page.locator("#probe a").count(), 0)

    def test_the_picture_keeps_its_geometry_and_the_dot_stays_on_it(self):
        self._mount("name presence")
        self.page.evaluate(
            "(uid) => Alpine.store('presence').setLocalStatus(uid, 'online')",
            self.user.id,
        )
        self.page.wait_for_timeout(300)

        geometry = self.page.evaluate(
            """
            () => {
              const el = document.getElementById('probe');
              const box = el.firstElementChild;
              const dot = el.querySelector('[data-presence-dot]');
              const b = box.getBoundingClientRect();
              const d = dot.getBoundingClientRect();
              return {
                box: { w: Math.round(b.width), h: Math.round(b.height) },
                // The dot must hug the picture's bottom-right corner, not the
                // row's - that is what `relative` moving to the box buys.
                dotOffset: { x: Math.round(b.right - d.right), y: Math.round(b.bottom - d.bottom) },
                rowWiderThanBox: el.getBoundingClientRect().width > b.width,
                ring: [...box.querySelector('span').classList].filter((c) => c.startsWith('ring-')),
                dotColour: [...dot.classList].filter((c) => c.startsWith('bg-')),
              };
            }
            """
        )

        # size="md" is w-10 h-10 = 40px, unchanged by the name beside it.
        self.assertEqual(geometry["box"], {"w": 40, "h": 40})
        self.assertEqual(geometry["dotOffset"], {"x": 0, "y": 0})
        self.assertTrue(geometry["rowWiderThanBox"], "the name took no room")
        self.assertIn("ring-success", geometry["ring"])
        self.assertIn("bg-success", geometry["dotColour"])
