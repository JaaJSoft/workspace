"""E2E test: a reactive ``:data-lucide`` binding re-renders the drawn icon.

The guest vote buttons on the public shared-poll page cycle their icon
through circle → check → help-circle → x by rewriting ``data-lucide`` on a
*stable* node (the ``x-for`` row is keyed by slot, so the svg itself never
gets re-created). Lucide only hydrates a placeholder once, so without the
attribute branch of ``observeLucideIcons`` the drawn paths freeze on the
initial circle while the attribute keeps up appearances - which is why the
page used to force ``lucide.createIcons()`` after every vote click.

The broken variant *does* keep the ``data-lucide`` attribute up to date on
the stale svg, so the assertions target what Lucide actually re-draws: the
icon-name class (``lucide-check``) and the svg's inner content - never the
attribute alone.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from playwright.sync_api import expect

from workspace.calendar.models import Poll, PollSlot
from workspace.common.tests.e2e.base import PlaywrightTestCase


class PollVoteIconRerenderTests(PlaywrightTestCase):
    def test_vote_icon_redraws_when_cycled_in_place(self):
        owner = self.create_user(username="pollowner")
        poll = Poll.objects.create(title="Team lunch", created_by=owner)
        PollSlot.objects.create(
            poll=poll, start=timezone.now() + timedelta(days=1), position=0
        )

        self.page.goto(
            f"{self.live_server_url}/calendar/polls/shared/{poll.share_token}"
        )
        expect(self.page.locator("h1")).to_have_text("Team lunch")

        # The editable "You" row is the only one carrying the pencil marker.
        vote_button = self.page.locator('tr:has([data-lucide="pencil"]) button').first

        # Precondition: Lucide hydrated the unvoted state. If this times
        # out, the icon CDN did not load - fail loudly here rather than
        # misattribute it below.
        circle = vote_button.locator("svg.lucide-circle")
        expect(circle).to_be_visible()
        circle_paths = circle.inner_html()

        # First click: "" → "yes". The binding rewrites data-lucide on the
        # already-drawn svg; the observer must rebuild it as a check mark.
        vote_button.click()
        check = vote_button.locator("svg.lucide-check")
        expect(check).to_be_visible()
        self.assertNotEqual(
            check.inner_html(),
            circle_paths,
            "the vote icon's svg content did not change on click - "
            "Lucide is still showing the stale hydrated icon",
        )
        # The stale icon-name class must not pile up on the replacement.
        self.assertNotIn("lucide-circle", check.get_attribute("class"))

        # Second click: "yes" → "maybe". Re-renders must keep working once
        # the first replacement happened (the replacement svg keeps the
        # Alpine binding alive).
        vote_button.click()
        expect(vote_button.locator("svg.lucide-help-circle")).to_be_visible()

        # Loop guard: a same-value re-hydration cycle would keep replacing
        # the svg forever. Give any runaway loop time to manifest, then
        # check the button still holds exactly one settled svg.
        self.page.wait_for_timeout(300)
        self.assertEqual(vote_button.locator("svg").count(), 1)
        expect(vote_button.locator("svg.lucide-help-circle")).to_be_visible()
