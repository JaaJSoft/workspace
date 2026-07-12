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
        self.assertEqual(set(ids), {"rename", "manage_labels"})


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
            set(ids), {"edit", "move", "assign", "set_due", "set_labels", "delete"}
        )

    def test_archived_project_freezes_tasks(self):
        self.assertEqual(
            action_ids(self.member, self.task, role="member", archived=True), []
        )
