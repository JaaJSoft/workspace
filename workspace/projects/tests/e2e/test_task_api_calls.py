"""E2E: the board's own JavaScript, a real browser and the real task API.

The JS unit tests answer the board's requests with fakes, so they hold the
shape of what it sends and nothing of what the server makes of it. Here the
page's own component sends each request and the test reads the stored rows.
"""

from __future__ import annotations

import datetime

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.projects.models import TaskWatcher
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

BOARD = "Alpine.$data(document.querySelector('[x-data*=\"projectBoard\"]'))"


class TaskApiCallsTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.project = create_project(self.user, name="Website")
        self.backlog = self.project.statuses.get(name="Backlog")
        self.todo = self.project.statuses.get(name="To do")
        self.t1 = create_task(self.project, self.user, title="Write the brief")
        self.t2 = create_task(self.project, self.user, title="Plan the launch")
        self.t3 = create_task(self.project, self.user, title="Book the venue")
        self.login_as(self.user)

    def _open(self, path="/backlog"):
        self.page.goto(f"{self.live_server_url}/projects/{self.project.uuid}{path}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        expect(
            self.page.locator("#project-content [data-task-uuid]").first
        ).to_be_visible()

    def _board(self, script):
        """Run *script* with ``board`` bound to the page's board component."""
        return self.page.evaluate(f"async () => {{ const board = {BOARD}; {script} }}")

    def _select(self, *tasks):
        uuids = [str(task.uuid) for task in tasks]
        self._board(f"board.selected = {uuids!r}; await board.loadBulkActions();")

    def _link_results(self, query):
        """What the open panel offers to link for *query*: title and project."""
        return self.page.evaluate(
            """async (query) => {
                const panel = Alpine.$data(document.querySelector('[x-data="taskPanel()"]'));
                panel.linkQuery = query;
                await panel.searchLinkTasks();
                return panel.linkResults.map((r) => [r.title, r.project.name]);
            }""",
            query,
        )

    def _titles(self, column):
        return list(
            self.project.tasks.filter(status=column)
            .order_by("position")
            .values_list("title", flat=True)
        )

    def test_a_drop_orders_the_column(self):
        self._open()
        order = [str(self.t3.uuid), str(self.t1.uuid)]
        self._board(f"await board.saveOrder('{self.backlog.uuid}', {order!r});")
        self.assertEqual(
            self._titles(self.backlog),
            ["Book the venue", "Write the brief", "Plan the launch"],
        )

    def test_the_toolbar_learns_what_the_selection_may_do_and_edits_it(self):
        self._open()
        self._select(self.t1, self.t2)
        self.assertTrue(self._board("return board.bulkCan('set_priority');"))

        self._board("await board.bulkPriority('urgent');")

        priorities = dict(self.project.tasks.values_list("title", "priority"))
        self.assertEqual(
            priorities,
            {
                "Write the brief": "urgent",
                "Plan the launch": "urgent",
                "Book the venue": "medium",
            },
        )

    def test_a_due_date_one_task_refuses_changes_none_and_says_so(self):
        self.t1.start_date = datetime.date(2030, 6, 1)
        self.t1.save(update_fields=["start_date"])
        self._open()
        self._select(self.t1, self.t2)

        warnings = self._board(
            "const said = [];"
            "window.AppAlert = { warning: (m) => said.push(m), error: (m) => said.push('error: ' + m) };"
            "await board.bulkDueDate('2030-01-15');"
            "return said;"
        )

        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("1 task starts after that due date", warnings[0])
        self.assertFalse(self.project.tasks.filter(due_date__isnull=False).exists())

    def test_the_selection_moves_to_a_column_and_is_deleted(self):
        self._open()
        self._board(
            f"await board.moveTasks({[str(self.t1.uuid), str(self.t2.uuid)]!r}, '{self.todo.uuid}');"
        )
        self.assertEqual(
            self._titles(self.todo), ["Write the brief", "Plan the launch"]
        )

        self._select(self.t3)
        # Not awaited: it waits on the confirmation the next line gives.
        self._board("board.bulkDeleteSelected();")
        with self.page.expect_response(lambda r: r.url.endswith("/tasks/batch")):
            self.page.locator("#app-dialog-confirm-ok").click()
        self.assertEqual(
            sorted(self.project.tasks.values_list("title", flat=True)),
            ["Plan the launch", "Write the brief"],
        )

    def test_the_panel_saves_a_field_finds_a_task_to_link_and_sets_a_watch(self):
        self._open(f"/backlog?task={self.t1.uuid}")
        panel = self.page.locator("#task-panel")
        expect(panel).to_contain_text("Write the brief")

        self._board(f"await board.patchTask('{self.t1.uuid}', {{ priority: 'high' }});")
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.priority, "high")

        expect(panel).to_contain_text("Write the brief")
        self.assertEqual(
            self._link_results(self.t2.reference), [["Plan the launch", "Website"]]
        )
        # A task is never offered as a link to itself.
        self.assertEqual(self._link_results(self.t1.reference), [])

        with self.page.expect_response(lambda r: r.url.endswith("/watch")) as saved:
            self.page.evaluate(
                "Alpine.$data(document.querySelector('[x-data^=\"taskWatch\"]')).set('muted')"
            )
        self.assertEqual(saved.value.status, 204)
        self.assertTrue(
            TaskWatcher.objects.filter(
                task=self.t1, user=self.user, muted=True
            ).exists()
        )
