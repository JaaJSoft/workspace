from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.core.module_registry import SearchScope
from workspace.projects.models import ProjectMember
from workspace.projects.search import search_project_tasks, search_projects
from workspace.projects.services.projects import (
    create_project,
    get_or_create_personal_project,
)
from workspace.projects.services.tasks import create_task

User = get_user_model()


class ProjectSearchScopeTests(TestCase):
    """Mine is the user's personal project: every other board is shared, the
    ones they created included."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", password="x")
        bob = User.objects.create_user(username="bob", password="x")
        team = Group.objects.create(name="Team")
        cls.alice.groups.add(team)

        cls.personal = get_or_create_personal_project(cls.alice)
        own_team_board = create_project(cls.alice, name="Zanzibar launch")
        member_board = create_project(bob, name="Zanzibar ops")
        ProjectMember.objects.create(project=member_board, user=cls.alice)
        group_board = create_project(bob, name="Zanzibar group", groups=[team])
        # Bob's personal project, opened to alice, is still Bob's.
        bob_personal = get_or_create_personal_project(bob)
        ProjectMember.objects.create(project=bob_personal, user=cls.alice)

        for project in (
            cls.personal,
            own_team_board,
            member_board,
            group_board,
            bob_personal,
        ):
            create_task(
                project,
                project.created_by,
                title=f"Zanzibar task in {project.name} {project.key}",
            )

    def test_all_tasks_reach_every_accessible_board(self):
        hits = search_project_tasks("zanzibar", self.alice, 10, scope=SearchScope.ALL)
        self.assertEqual(len(hits), 5)

    def test_mine_tasks_stay_in_the_personal_project(self):
        hits = search_project_tasks("zanzibar", self.alice, 10, scope=SearchScope.MINE)
        self.assertEqual(
            {h.url.split("?")[0] for h in hits}, {f"/projects/{self.personal.uuid}"}
        )

    def test_mine_projects_drop_the_shared_boards(self):
        all_names = {
            p.name
            for p in search_projects("zanzibar", self.alice, 10, scope=SearchScope.ALL)
        }
        self.assertEqual(
            all_names, {"Zanzibar launch", "Zanzibar ops", "Zanzibar group"}
        )
        self.assertEqual(
            search_projects("zanzibar", self.alice, 10, scope=SearchScope.MINE), []
        )

    def test_mine_projects_find_the_personal_project(self):
        [hit] = search_projects("personal", self.alice, 10, scope=SearchScope.MINE)
        self.assertEqual(hit.uuid, str(self.personal.uuid))
