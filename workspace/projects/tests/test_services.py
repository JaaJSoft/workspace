from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.projects.models import Project, ProjectMember, TaskStatus
from workspace.projects.services.members import (
    LastAdminError,
    ProjectRuleError,
    add_member,
    change_member_role,
    remove_member,
)
from workspace.projects.services.projects import get_or_create_personal_project

from .base import ProjectTestMixin

User = get_user_model()


class CreateProjectTests(ProjectTestMixin, TestCase):
    def test_seeds_default_statuses_in_order(self):
        statuses = list(self.project.statuses.order_by("position"))
        self.assertEqual(
            [(s.name, s.category) for s in statuses],
            [
                ("Backlog", TaskStatus.Category.BACKLOG),
                ("To do", TaskStatus.Category.ACTIVE),
                ("In progress", TaskStatus.Category.ACTIVE),
                ("Done", TaskStatus.Category.DONE),
            ],
        )

    def test_creator_becomes_admin_member(self):
        self.assertEqual(self.admin_membership.role, ProjectMember.Role.ADMIN)
        self.assertIsNone(self.admin_membership.left_at)

    def test_created_by_is_audit_only(self):
        self.assertEqual(self.project.created_by, self.admin)
        self.assertEqual(self.project.type, Project.Type.KANBAN)


class PersonalProjectTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )

    def test_lazily_created_once(self):
        first = get_or_create_personal_project(self.user)
        second = get_or_create_personal_project(self.user)
        self.assertEqual(first.uuid, second.uuid)
        self.assertEqual(first.type, Project.Type.PERSONAL)
        self.assertEqual(first.name, "Personal")
        self.assertEqual(first.statuses.count(), 4)

    def test_personal_project_rejects_members(self):
        personal = get_or_create_personal_project(self.user)
        other = User.objects.create_user(
            username="bob", email="bob@test.com", password="pass123"
        )
        with self.assertRaises(ProjectRuleError):
            add_member(personal, other)


class MembershipGuardTests(ProjectTestMixin, TestCase):
    def test_add_member_reactivates_departed_row(self):
        remove_member(self.membership)
        self.membership.refresh_from_db()
        self.assertIsNotNone(self.membership.left_at)
        member = add_member(self.project, self.member, role=ProjectMember.Role.ADMIN)
        self.assertEqual(member.uuid, self.membership.uuid)
        self.assertIsNone(member.left_at)
        self.assertEqual(member.role, ProjectMember.Role.ADMIN)

    def test_cannot_demote_last_admin(self):
        with self.assertRaises(LastAdminError):
            change_member_role(self.admin_membership, ProjectMember.Role.MEMBER)

    def test_cannot_remove_last_admin(self):
        with self.assertRaises(LastAdminError):
            remove_member(self.admin_membership)

    def test_demote_allowed_when_other_admin_exists(self):
        change_member_role(self.membership, ProjectMember.Role.ADMIN)
        changed = change_member_role(self.admin_membership, ProjectMember.Role.MEMBER)
        self.assertEqual(changed.role, ProjectMember.Role.MEMBER)

    def test_remove_regular_member(self):
        removed = remove_member(self.membership)
        self.assertIsNotNone(removed.left_at)
