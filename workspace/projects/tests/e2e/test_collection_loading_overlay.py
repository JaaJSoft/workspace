"""E2E test: swapping the task collection shows a loading overlay until
the new fragment is bound.

The overlay sits next to ``#task-collection`` so it survives the swap. It
appears on the ``ajax:send`` of a request that targets the collection or
the whole ``#project-content`` (a filter change swaps the former, a sprint
switch or a view navigation the latter, and both replace the collection),
and goes away on ``project-fragment-bound``, the event the new fragment
fires once Alpine has bound it. A request for anything else (the task
panel) must leave the overlay alone.
"""

from __future__ import annotations

import time

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task


class CollectionLoadingOverlayTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.project = create_project(self.user, name="Website")
        # A new task lands in the backlog column, which the board does not
        # show: the board card needs an active status, the backlog its own.
        active = (
            self.project.statuses.filter(category="active").order_by("position").first()
        )
        self.task = create_task(
            self.project, self.user, title="Write the brief", status=active
        )
        create_task(self.project, self.user, title="Plan the launch")
        self.board_url = f"{self.live_server_url}/projects/{self.project.uuid}/board"

    def _wait_for(self, held, timeout=5):
        deadline = time.monotonic() + timeout
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        self.assertTrue(held, "the request never went out")

    def _hold_fragment_requests(self, pattern):
        # Only the alpine-ajax fragment request is held: the full page
        # load that opens the board matches the same URL.
        held = []

        def hold(route):
            if route.request.headers.get("x-alpine-request"):
                held.append(route)
            else:
                route.continue_()

        self.page.route(pattern, hold)
        return held

    def _open_board(self):
        self.login_as(self.user)
        self.page.goto(self.board_url)
        expect(self.page.locator("[data-task-uuid]")).to_have_count(1)
        # The debug toolbar overlays the pane and swallows pointer events.
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        return self.page.get_by_test_id("task-collection-loading")

    def _search(self, text):
        self.page.get_by_placeholder("Search tasks").fill(text)

    def test_overlay_covers_a_filter_request_then_clears(self):
        overlay = self._open_board()
        expect(overlay).to_be_hidden()

        held = self._hold_fragment_requests("**/board*")
        self._search("brief")
        self._wait_for(held)
        expect(overlay).to_be_visible()

        held[0].continue_()
        expect(self.page.locator("#task-collection")).to_contain_text("Write the brief")
        expect(overlay).to_be_hidden()

    def test_overlay_stays_invisible_for_the_first_200ms(self):
        # The fade-in is delayed so a collection that answers at once never
        # flashes a half-drawn veil. Playwright's visibility check ignores
        # opacity, so the computed style is what pins the delay down.
        overlay = self._open_board()

        held = self._hold_fragment_requests("**/board*")
        self._search("brief")
        self._wait_for(held)
        expect(overlay).to_be_visible()
        self.assertEqual(overlay.evaluate("el => getComputedStyle(el).opacity"), "0")
        self.page.wait_for_timeout(600)
        self.assertEqual(overlay.evaluate("el => getComputedStyle(el).opacity"), "1")

        held[0].continue_()
        expect(overlay).to_be_hidden()

    def test_overlay_clears_when_a_filter_request_fails(self):
        overlay = self._open_board()

        held = self._hold_fragment_requests("**/board*")
        self._search("brief")
        self._wait_for(held)
        expect(overlay).to_be_visible()

        held[0].fulfill(status=500, body="boom")
        expect(overlay).to_be_hidden()
        expect(self.page.locator("[data-task-uuid]")).to_have_count(1)

    def test_overlay_covers_a_view_navigation_then_clears(self):
        # A drawer link swaps the whole #project-content, collection
        # included: the same veil covers that wait.
        overlay = self._open_board()

        held = self._hold_fragment_requests("**/backlog*")
        self.page.locator(r'a[x-target\.push][href$="/backlog"]').click()
        self._wait_for(held)
        expect(overlay).to_be_visible()

        held[0].continue_()
        expect(self.page.locator("#task-collection")).to_contain_text("Plan the launch")
        expect(overlay).to_be_hidden()
        self.assertTrue(self.page.url.endswith("/backlog"))

    def test_overlay_clears_on_a_view_without_a_collection(self):
        # The overview has no #task-collection to announce itself, so the
        # content root must clear the flag or the veil would come back
        # with the next board render.
        overlay = self._open_board()

        held = self._hold_fragment_requests(f"**/projects/{self.project.uuid}")
        self.page.locator(
            rf'a[x-target\.push][href$="/projects/{self.project.uuid}"]'
        ).click()
        self._wait_for(held)
        expect(overlay).to_be_visible()

        held[0].continue_()
        expect(overlay).to_be_hidden()
        expect(self.page.locator("#project-content")).not_to_contain_text(
            "Search tasks"
        )
        self.assertEqual(
            self.page.evaluate(
                "Alpine.$data(document.querySelector('[x-data^=projectBoard]')).collectionLoading"
            ),
            False,
        )

    def test_a_task_panel_request_does_not_show_the_overlay(self):
        overlay = self._open_board()

        held = []
        self.page.route("**/panel*", lambda route: held.append(route))
        self.page.locator(f'[data-task-uuid="{self.task.uuid}"]').click()
        self._wait_for(held)
        expect(overlay).to_be_hidden()
        held[0].continue_()
