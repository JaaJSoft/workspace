from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from workspace.projects.models import Project, ProjectMember
from workspace.projects.queries import get_project_role, user_project_ids

User = get_user_model()


class UserProjectIdsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin1", email="admin1@test.com", password="pass123"
        )
        self.member = User.objects.create_user(
            username="member1", email="member1@test.com", password="pass123"
        )
        self.outsider = User.objects.create_user(
            username="outsider1", email="outsider1@test.com", password="pass123"
        )
        self.project = Project.objects.create(name="Website", created_by=self.admin)
        ProjectMember.objects.create(
            project=self.project, user=self.admin, role=ProjectMember.Role.ADMIN
        )
        ProjectMember.objects.create(project=self.project, user=self.member)

    def test_active_member_sees_project(self):
        self.assertIn(self.project.uuid, list(user_project_ids(self.member)))

    def test_outsider_sees_nothing(self):
        self.assertEqual(list(user_project_ids(self.outsider)), [])

    def test_departed_member_excluded(self):
        ProjectMember.objects.filter(user=self.member).update(left_at=timezone.now())
        self.assertEqual(list(user_project_ids(self.member)), [])

    def test_group_member_sees_project(self):
        group = Group.objects.create(name="devs")
        self.outsider.groups.add(group)
        self.project.group = group
        self.project.save(update_fields=["group"])
        self.assertIn(self.project.uuid, list(user_project_ids(self.outsider)))

    def test_role_admin_filter(self):
        self.assertIn(
            self.project.uuid, list(user_project_ids(self.admin, role="admin"))
        )
        self.assertEqual(list(user_project_ids(self.member, role="admin")), [])

    def test_group_access_never_grants_admin(self):
        group = Group.objects.create(name="devs")
        self.outsider.groups.add(group)
        self.project.group = group
        self.project.save(update_fields=["group"])
        self.assertEqual(list(user_project_ids(self.outsider, role="admin")), [])

    def test_archived_project_still_visible(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.assertIn(self.project.uuid, list(user_project_ids(self.member)))


class GetProjectRoleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin1", email="admin1@test.com", password="pass123"
        )
        self.member = User.objects.create_user(
            username="member1", email="member1@test.com", password="pass123"
        )
        self.outsider = User.objects.create_user(
            username="outsider1", email="outsider1@test.com", password="pass123"
        )
        self.project = Project.objects.create(name="Website", created_by=self.admin)
        ProjectMember.objects.create(
            project=self.project, user=self.admin, role=ProjectMember.Role.ADMIN
        )
        ProjectMember.objects.create(project=self.project, user=self.member)

    def test_roles(self):
        self.assertEqual(get_project_role(self.admin, self.project), "admin")
        self.assertEqual(get_project_role(self.member, self.project), "member")
        self.assertIsNone(get_project_role(self.outsider, self.project))

    def test_departed_member_has_no_role(self):
        ProjectMember.objects.filter(user=self.member).update(left_at=timezone.now())
        self.assertIsNone(get_project_role(self.member, self.project))

    def test_group_grants_member_role(self):
        group = Group.objects.create(name="devs")
        self.outsider.groups.add(group)
        self.project.group = group
        self.project.save(update_fields=["group"])
        self.assertEqual(get_project_role(self.outsider, self.project), "member")

    def test_membership_row_wins_over_group(self):
        group = Group.objects.create(name="devs")
        self.admin.groups.add(group)
        self.project.group = group
        self.project.save(update_fields=["group"])
        self.assertEqual(get_project_role(self.admin, self.project), "admin")
