import re

import orjson
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.ai.tests.agents import tool_context
from workspace.core.sse_registry import read_user_events
from workspace.core.views.sse import _emit
from workspace.projects.ai_tools import MoveTaskParams, ProjectsToolProvider
from workspace.projects.services.tasks import create_task
from workspace.projects.sse_provider import (
    CHANGED,
    SLUG,
    ProjectsSSEProvider,
    _next_revision,
    project_revision,
)

from .base import ProjectTestMixin


def announced(user):
    entries, _cursor = read_user_events(SLUG, user.pk, 0)
    return [payload for _seq, payload in entries]


class _LiveUpdatesBase(ProjectTestMixin):
    def setUp(self):
        cache.clear()
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        self.api = f"/api/v1/projects/{self.project.uuid}"

    def tearDown(self):
        cache.clear()


class ProjectChangeApiTests(_LiveUpdatesBase, APITestCase):
    def _patch_task(self, user, **data):
        self.client.force_authenticate(user)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.patch(
                f"{self.api}/tasks/{self.task.uuid}", data, format="json"
            )

    def test_a_task_change_reaches_every_member_the_author_included(self):
        """The author's other tabs follow too; their own tab skips the echo."""
        response = self._patch_task(self.member, title="Ship it today")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for user in (self.admin, self.member):
            [event] = announced(user)
            self.assertEqual(event["type"], CHANGED)
            self.assertEqual(event["project"], str(self.project.uuid))
            self.assertEqual(event["task"], str(self.task.uuid))
        self.assertEqual(announced(self.outsider), [])

    def test_the_event_carries_no_content(self):
        """Pages re-render through their own access-checked views."""
        self._patch_task(self.member, title="Secret roadmap")
        [event] = announced(self.admin)
        self.assertEqual(set(event), {"type", "project", "task", "revision"})

    def test_users_reaching_the_project_through_a_group_are_told(self):
        team = Group.objects.create(name="Team")
        self.outsider.groups.add(team)
        self.project.groups.add(team)

        self._patch_task(self.admin, title="Ship it today")

        self.assertEqual(len(announced(self.outsider)), 1)

    def test_a_board_wide_change_names_no_task(self):
        other = create_task(self.project, self.admin, title="Other")
        backlog = self.project.statuses.get(name="Backlog")
        self.client.force_authenticate(self.member)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{self.api}/tasks/reorder",
                {
                    "status": str(backlog.uuid),
                    "order": [str(other.uuid), str(self.task.uuid)],
                },
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        [event] = announced(self.admin)
        self.assertIsNone(event["task"])

    def test_a_refused_write_announces_nothing(self):
        self.client.force_authenticate(self.member)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{self.api}/tasks/reorder",
                {"status": "not-a-uuid", "order": []},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(announced(self.admin), [])

    def test_a_read_announces_nothing(self):
        self.client.force_authenticate(self.member)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(f"{self.api}/tasks")
        self.assertEqual(announced(self.admin), [])

    def test_a_personal_notification_level_announces_nothing(self):
        self.client.force_authenticate(self.member)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.put(
                f"{self.api}/notification-level", {"level": "none"}, format="json"
            )
        self.assertLess(response.status_code, 400)
        self.assertEqual(announced(self.admin), [])

    def test_renaming_the_project_is_announced(self):
        self.client.force_authenticate(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(
                f"{self.api}", {"name": "Website v2"}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        [event] = announced(self.member)
        self.assertIsNone(event["task"])

    def test_a_member_stream_delivers_the_change_as_projects_changed(self):
        """The name board.js listens to, once the stream prefixed it with the slug."""
        stream = ProjectsSSEProvider(self.member, None)
        self._patch_task(self.admin, title="Ship it today")
        [(name, payload, event_id)] = stream.poll("dirty")

        frame = _emit(SLUG, name, payload, event_id, {})
        data = orjson.loads(frame.split("data: ", 1)[1])
        self.assertEqual(data["event"], "projects.changed")
        self.assertEqual(data["data"]["task"], str(self.task.uuid))


class AssistantChangeTests(_LiveUpdatesBase, TestCase):
    def test_a_task_moved_by_the_assistant_is_announced(self):
        with self.captureOnCommitCallbacks(execute=True):
            ProjectsToolProvider().move_task(
                tool_context(user=self.member),
                MoveTaskParams(task_uuid=str(self.task.uuid), status="Done"),
            )
        [event] = announced(self.admin)
        self.assertEqual(event["task"], str(self.task.uuid))


class PageRevisionTests(_LiveUpdatesBase, TestCase):
    def _rendered_revision(self, path):
        self.client.force_login(self.member)
        response = self.client.get(path, headers={"X-Alpine-Request": "true"})
        self.assertEqual(response.status_code, 200)
        match = re.search(r'data-project-revision="(\d+)"', response.content.decode())
        return int(match.group(1))

    def _change(self):
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.patch(
                f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}",
                {"title": "Ship it today"},
                content_type="application/json",
            )
        [event] = announced(self.member)
        return event["revision"]

    def test_a_page_rendered_before_a_change_is_behind_it(self):
        shown = self._rendered_revision(f"/projects/{self.project.uuid}/board")
        self.assertGreater(self._change(), shown)

    def test_a_page_rendered_after_a_change_covers_it(self):
        """The author's own refresh lands here, so their tab skips the echo."""
        revision = self._change()
        shown = self._rendered_revision(f"/projects/{self.project.uuid}/board")
        self.assertGreaterEqual(shown, revision)

    def test_the_task_panel_carries_its_revision(self):
        revision = self._change()
        shown = self._rendered_revision(
            f"/projects/{self.project.uuid}/tasks/{self.task.uuid}/panel"
        )
        self.assertGreaterEqual(shown, revision)

    def test_a_lost_counter_restarts_above_every_revision_shown(self):
        """An evicted key must not leave pages believing they are up to date."""
        shown = _next_revision(self.project.uuid)
        cache.clear()
        self.assertGreater(project_revision(self.project.uuid), shown)
        self.assertGreater(_next_revision(self.project.uuid), shown)
