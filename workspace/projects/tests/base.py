from django.contrib.auth import get_user_model

from workspace.projects.models import ProjectMember
from workspace.projects.services.members import add_member
from workspace.projects.services.projects import create_project

User = get_user_model()


class ProjectTestMixin:
    """Creates admin/member/outsider users and one kanban project."""

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
        self.project = create_project(self.admin, name="Website")
        self.membership = add_member(self.project, self.member)
        self.admin_membership = ProjectMember.objects.get(
            project=self.project, user=self.admin
        )
