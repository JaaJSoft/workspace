from django.test import TestCase
from django.utils import timezone

from workspace.projects.actions import ProjectActionRegistry
from workspace.projects.services.projects import get_or_create_personal_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


def action_ids(user, obj, *, role, archived=False):
    return [
        a["id"]
        for a in ProjectActionRegistry.get_available_actions(
            user, obj, role=role, archived=archived
        )
    ]


class ProjectActionTests(ProjectTestMixin, TestCase):
    def test_admin_gets_all_project_actions(self):
        ids = action_ids(self.admin, self.project, role="admin")
        self.assertEqual(
            set(ids),
            {
                "rename",
                "manage_members",
                "manage_labels",
                "manage_epics",
                "attach_group",
                "archive",
                "delete",
            },
        )

    def test_member_gets_no_project_admin_actions(self):
        self.assertEqual(action_ids(self.member, self.project, role="member"), [])

    def test_no_role_gets_nothing(self):
        self.assertEqual(action_ids(self.outsider, self.project, role=None), [])

    def test_archived_project_only_offers_unarchive(self):
        self.project.archived_at = timezone.now()
        ids = action_ids(self.admin, self.project, role="admin", archived=True)
        self.assertIn("unarchive", ids)
        self.assertIn("delete", ids)
        self.assertNotIn("rename", ids)
        self.assertNotIn("archive", ids)

    def test_personal_project_hides_sharing_and_lifecycle(self):
        personal = get_or_create_personal_project(self.admin)
        ids = action_ids(self.admin, personal, role="admin")
        self.assertEqual(set(ids), {"rename", "manage_labels", "manage_epics"})


class RegistryTests(TestCase):
    def test_delete_action_registered_for_both_target_types(self):
        by_target = {
            target: [
                a.id for a in ProjectActionRegistry.all() if target in a.target_types
            ]
            for target in ("project", "task")
        }
        self.assertIn("delete", by_target["project"])
        self.assertIn("delete", by_target["task"])


class TaskActionTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="t")

    def test_member_gets_all_task_actions(self):
        ids = action_ids(self.member, self.task, role="member")
        self.assertEqual(
            set(ids),
            {
                "edit",
                "move",
                "assign",
                "set_due",
                "set_priority",
                "set_labels",
                "set_epic",
                "attach",
                "link",
                "link_file",
                "comment",
                "delete",
            },
        )

    def test_archived_project_freezes_tasks(self):
        self.assertEqual(
            action_ids(self.member, self.task, role="member", archived=True), []
        )


class RegistryGatesTheWritePathTests(ProjectTestMixin, TestCase):
    """An endpoint performs an action only when the registry offers it, so a
    rule added to an action reaches every write path without a second copy."""

    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="t")
        self.task_url = f"/api/v1/projects/tasks/{self.task.uuid}"

    def _withdrawn(self, action_id, target):
        from unittest.mock import patch

        action = ProjectActionRegistry.action_for(action_id, target)
        return patch.object(type(action), "is_available", return_value=False)

    def test_a_withdrawn_task_action_is_refused_by_its_endpoint(self):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(self.member)
        with self._withdrawn("assign", self.task):
            assign = client.patch(
                self.task_url, {"assignees": [self.member.pk]}, format="json"
            )
            rename = client.patch(self.task_url, {"title": "renamed"}, format="json")

        self.assertEqual(assign.status_code, 403)
        self.assertFalse(self.task.assignees.exists())
        self.assertEqual(rename.status_code, 200)

    def test_a_withdrawn_project_action_is_refused_by_its_endpoint(self):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(self.admin)
        with self._withdrawn("manage_labels", self.project):
            response = client.post(
                f"/api/v1/projects/{self.project.uuid}/labels",
                {"name": "Bug", "color": "#ff0000"},
                format="json",
            )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.project.labels.filter(name="Bug").exists())

    def test_an_id_shared_by_a_project_and_a_task_action_resolves_per_target(self):
        project_delete = ProjectActionRegistry.action_for("delete", self.project)
        task_delete = ProjectActionRegistry.action_for("delete", self.task)

        self.assertIn("project", project_delete.target_types)
        self.assertIn("task", task_delete.target_types)
        self.assertTrue(
            ProjectActionRegistry.is_action_available(
                "delete", self.admin, self.project, role="admin", archived=False
            )
        )
