from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from workspace.common.search import fts5_available
from workspace.projects.models import Project, ProjectMember, Task, TaskStatus
from workspace.projects.services.search import search_projects_qs, search_tasks_qs

User = get_user_model()


def make_project(owner, *members, name="Board", description=""):
    project = Project.objects.create(
        name=name, description=description, created_by=owner
    )
    for user in (owner, *members):
        ProjectMember.objects.create(project=project, user=user)
    return project


def make_task(project, title, description=""):
    status = project.statuses.first()
    if status is None:
        status = TaskStatus.objects.create(
            project=project, name="Todo", category=TaskStatus.Category.BACKLOG
        )
    return Task.objects.create(
        project=project,
        title=title,
        description=description,
        status=status,
        created_by=project.created_by,
    )


class FtsSchemaTests(TestCase):
    def test_sqlite_fts_tables_exist(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-only schema check")
        with connection.cursor() as c:
            for table in ("projects_project_fts", "projects_task_fts"):
                c.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s",
                    [table],
                )
                self.assertIsNotNone(c.fetchone(), table)

    def test_sqlite_triggers_track_insert_update_delete(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite-only trigger check")
        alice = User.objects.create_user(username="a", email="a@x.io")
        project = make_project(alice)
        task = make_task(project, "the zanzibar report")

        def match(term):
            with connection.cursor() as c:
                c.execute(
                    "SELECT rowid FROM projects_task_fts "
                    "WHERE projects_task_fts MATCH %s",
                    (f'"{term}"',),
                )
                return c.fetchone()

        self.assertIsNotNone(match("zanzibar"))

        task.title = "the yokohama report"
        task.save(update_fields=["title"])
        self.assertIsNone(match("zanzibar"))
        self.assertIsNotNone(match("yokohama"))

        task.delete()
        self.assertIsNone(match("yokohama"))


class ProjectSearchServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", email="al@x.io")
        cls.bob = User.objects.create_user(username="bob", email="bo@x.io")
        cls.shared = make_project(
            cls.alice, cls.bob, name="Kumquat launch", description="crate the boxes"
        )
        cls.bob_only = make_project(
            cls.bob, name="Secret kumquat plans", description=""
        )

    def test_finds_by_name(self):
        hits = list(search_projects_qs(self.alice, "kumquat"))
        self.assertEqual([p.uuid for p in hits], [self.shared.uuid])

    def test_finds_by_description(self):
        hits = list(search_projects_qs(self.alice, "boxes"))
        self.assertEqual([p.uuid for p in hits], [self.shared.uuid])

    def test_access_control_excludes_non_member_projects(self):
        self.assertEqual(len(list(search_projects_qs(self.bob, "kumquat"))), 2)
        self.assertEqual(len(list(search_projects_qs(self.alice, "kumquat"))), 1)

    def test_left_member_loses_access(self):
        membership = ProjectMember.objects.get(project=self.shared, user=self.alice)
        membership.left_at = timezone.now()
        membership.save(update_fields=["left_at"])
        self.assertEqual(list(search_projects_qs(self.alice, "kumquat")), [])

    def test_group_access_grants_visibility(self):
        from django.contrib.auth.models import Group

        group = Group.objects.create(name="devs")
        self.alice.groups.add(group)
        grouped = Project.objects.create(
            name="Grouped kumquat effort", created_by=self.bob, group=group
        )
        hits = [p.uuid for p in search_projects_qs(self.alice, "kumquat")]
        self.assertIn(grouped.uuid, hits)

    def test_archived_projects_excluded(self):
        Project.objects.filter(pk=self.shared.pk).update(archived_at=timezone.now())
        self.assertEqual(list(search_projects_qs(self.alice, "kumquat")), [])

    def test_name_outranks_description(self):
        if connection.vendor == "sqlite" and not fts5_available():
            self.skipTest("relevance ranking needs FTS5 on SQLite")
        in_description = make_project(
            self.alice, name="Misc", description="pretzel pretzel pretzel notes"
        )
        in_name = make_project(self.alice, name="Pretzel board")
        hits = [p.uuid for p in search_projects_qs(self.alice, "pretzel")]
        self.assertLess(hits.index(in_name.uuid), hits.index(in_description.uuid))

    def test_accent_insensitive(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required for the accent path")
        make_project(self.alice, name="Réunion générale")
        hits = list(search_projects_qs(self.alice, "reunion"))
        self.assertEqual(len(hits), 1)

    def test_blank_query_returns_no_rows(self):
        self.assertEqual(list(search_projects_qs(self.alice, "   ")), [])

    def test_malformed_query_does_not_crash(self):
        hits = list(search_projects_qs(self.alice, 'kumquat" -launch'))
        self.assertIsInstance(hits, list)


class TaskSearchServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="talice", email="ta@x.io")
        cls.bob = User.objects.create_user(username="tbob", email="tb@x.io")
        cls.shared = make_project(cls.alice, cls.bob, name="Shared")
        cls.bob_only = make_project(cls.bob, name="Private")
        cls.t_shared = make_task(
            cls.shared, "Order the walrus stickers", "vendor quote attached"
        )
        cls.t_private = make_task(cls.bob_only, "Secret walrus roadmap")

    def test_finds_by_title(self):
        hits = list(search_tasks_qs(self.alice, "walrus"))
        self.assertEqual([t.uuid for t in hits], [self.t_shared.uuid])

    def test_finds_by_description(self):
        hits = list(search_tasks_qs(self.alice, "vendor quote"))
        self.assertEqual([t.uuid for t in hits], [self.t_shared.uuid])

    def test_access_control(self):
        self.assertEqual(len(list(search_tasks_qs(self.bob, "walrus"))), 2)
        self.assertEqual(len(list(search_tasks_qs(self.alice, "walrus"))), 1)

    def test_tasks_of_archived_projects_excluded(self):
        Project.objects.filter(pk=self.shared.pk).update(archived_at=timezone.now())
        self.assertEqual(list(search_tasks_qs(self.alice, "walrus")), [])

    def test_title_outranks_description(self):
        if connection.vendor == "sqlite" and not fts5_available():
            self.skipTest("relevance ranking needs FTS5 on SQLite")
        in_description = make_task(
            self.shared, "Misc", "flamingo flamingo flamingo details"
        )
        in_title = make_task(self.shared, "Flamingo cleanup")
        hits = [t.uuid for t in search_tasks_qs(self.alice, "flamingo")]
        self.assertLess(hits.index(in_title.uuid), hits.index(in_description.uuid))
