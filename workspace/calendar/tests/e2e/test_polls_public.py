"""Smoke test for the public shared-poll page.

This is the simplest possible end-to-end flow: no login, no CSRF, no
session cookies, no ``base.html`` (the shared-poll template is
standalone). We create a ``Poll`` with two slots directly via the ORM,
open the guest URL, cast a vote, and verify the success banner.

If this test passes the whole Playwright + Django live-server + static
files pipeline is working end-to-end.
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from playwright.sync_api import expect

from workspace.calendar.models import Poll, PollSlot
from workspace.common.tests.e2e.base import PlaywrightTestCase


class SharedPollVotingTests(PlaywrightTestCase):

    def _make_open_poll(self, *, title: str = "Team lunch") -> Poll:
        """Create an open poll with two future slots, owned by a throwaway user."""
        owner = self.create_user(username="pollowner")
        poll = Poll.objects.create(title=title, created_by=owner)
        now = timezone.now()
        PollSlot.objects.create(poll=poll, start=now + timedelta(days=1), position=0)
        PollSlot.objects.create(poll=poll, start=now + timedelta(days=2), position=1)
        return poll

    def test_guest_can_view_and_vote_on_shared_poll(self):
        poll = self._make_open_poll(title="Team lunch")

        self.page.goto(f"{self.live_server_url}/calendar/polls/shared/{poll.share_token}")

        # Alpine populates the <h1> via x-text once the /api/v1/calendar/polls/shared/<token>
        # fetch resolves; waiting on it asserts that both the UI view and
        # the public API endpoint are wired up correctly.
        expect(self.page.locator("h1")).to_have_text("Team lunch")

        # Fill in the guest identity (name is required, email is optional).
        self.page.locator('input[placeholder="Your name"]').fill("Alice Guest")

        # The "You" row exposes one vote button per slot. We click the
        # first slot's vote button once: cycleVote() goes "" → "yes".
        # The row is identified by the "pencil" icon Lucide swaps into
        # the leading cell, but it's more robust to match by the Alpine
        # attribute ``@click="cycleVote(...)"`` — rendered into the DOM
        # as the button element itself. We simply grab all vote buttons
        # inside the "You" row, which is the last row when the poll is
        # open (bg-primary/5).
        vote_row = self.page.locator("tr.bg-primary\\/5")
        expect(vote_row).to_be_visible()
        vote_buttons = vote_row.locator("button")
        expect(vote_buttons).to_have_count(2)
        vote_buttons.nth(0).click()

        # Submit.
        self.page.get_by_role("button", name="Save my votes").click()

        # The success banner appears once the POST resolves.
        expect(
            self.page.get_by_text("Your votes have been saved!", exact=False)
        ).to_be_visible()

        # Verify the vote actually landed in the DB for extra confidence
        # — this guards against the UI silently swallowing errors.
        self.assertEqual(poll.slots.first().votes.filter(choice="yes").count(), 1)
