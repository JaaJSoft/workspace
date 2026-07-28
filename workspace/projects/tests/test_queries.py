from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from workspace.projects.models import Project, ProjectMember
from workspace.projects.queries import (
    get_project_role,
    project_users,
    user_project_ids,
)

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

    def test_departed_member_with_group_access_still_sees_project(self):
        group = Group.objects.create(name="devs")
        self.member.groups.add(group)
        self.project.group = group
        self.project.save(update_fields=["group"])
        ProjectMember.objects.filter(user=self.member).update(left_at=timezone.now())
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

    def test_departed_member_with_group_access_keeps_member_role(self):
        # Group access is independent of membership rows: leaving a project
        # does not revoke the access granted by the attached auth.Group
        # (files precedent).
        group = Group.objects.create(name="devs")
        self.member.groups.add(group)
        self.project.group = group
        self.project.save(update_fields=["group"])
        ProjectMember.objects.filter(user=self.member).update(left_at=timezone.now())
        self.assertEqual(get_project_role(self.member, self.project), "member")

    def test_membership_row_wins_over_group(self):
        group = Group.objects.create(name="devs")
        self.admin.groups.add(group)
        self.project.group = group
        self.project.save(update_fields=["group"])
        self.assertEqual(get_project_role(self.admin, self.project), "admin")


class ProjectUsersTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin1", email="admin1@test.com", password="pass123"
        )
        self.member = User.objects.create_user(
            username="member1", email="member1@test.com", password="pass123"
        )
        self.grouper = User.objects.create_user(
            username="grouper1", email="grouper1@test.com", password="pass123"
        )
        self.outsider = User.objects.create_user(
            username="outsider1", email="outsider1@test.com", password="pass123"
        )
        self.project = Project.objects.create(name="Website", created_by=self.admin)
        ProjectMember.objects.create(
            project=self.project, user=self.admin, role=ProjectMember.Role.ADMIN
        )
        ProjectMember.objects.create(project=self.project, user=self.member)

    def _attach_group(self, *users):
        group = Group.objects.create(name="devs")
        for user in users:
            user.groups.add(group)
        self.project.group = group
        self.project.save(update_fields=["group"])
        return group

    def test_individual_members_only(self):
        self.assertEqual(
            [u.pk for u in project_users(self.project)],
            [self.admin.pk, self.member.pk],
        )

    def test_group_users_included(self):
        self._attach_group(self.grouper)
        pks = [u.pk for u in project_users(self.project)]
        self.assertIn(self.grouper.pk, pks)
        self.assertNotIn(self.outsider.pk, pks)

    def test_departed_member_excluded(self):
        ProjectMember.objects.filter(user=self.member).update(left_at=timezone.now())
        self.assertNotIn(self.member.pk, [u.pk for u in project_users(self.project)])

    def test_member_also_in_group_deduplicated(self):
        self._attach_group(self.member)
        pks = [u.pk for u in project_users(self.project)]
        self.assertEqual(pks.count(self.member.pk), 1)

    def test_departed_member_with_group_access_included(self):
        self._attach_group(self.member)
        ProjectMember.objects.filter(user=self.member).update(left_at=timezone.now())
        self.assertIn(self.member.pk, [u.pk for u in project_users(self.project)])

    def test_sorted_by_username_case_insensitive(self):
        # Uppercase sorts before lowercase in a raw ASCII sort, so a
        # case-sensitive implementation would put Bravo1 first.
        bravo = User.objects.create_user(
            username="Bravo1", email="bravo1@test.com", password="pass123"
        )
        self._attach_group(self.grouper, bravo)
        self.assertEqual(
            [u.username for u in project_users(self.project)],
            ["admin1", "Bravo1", "grouper1", "member1"],
        )
