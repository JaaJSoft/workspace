from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.common.search import fts5_available
from workspace.projects.search import search_project_tasks
from workspace.projects.services.projects import create_project
from workspace.projects.services.search import reference_tasks_qs
from workspace.projects.services.tasks import create_task

User = get_user_model()


class ReferenceSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", email="al@x.io")
        cls.bob = User.objects.create_user(username="bob", email="bo@x.io")
        cls.project = create_project(cls.alice, name="Website Redesign")
        cls.task = create_task(cls.project, cls.alice, title="Fix the hero image")

    def test_reference_query_finds_the_task(self):
        hits = list(reference_tasks_qs(self.alice, f"{self.project.key}-1"))
        self.assertEqual([t.uuid for t in hits], [self.task.uuid])

    def test_reference_key_is_case_insensitive(self):
        hits = list(reference_tasks_qs(self.alice, f"{self.project.key.lower()}-1"))
        self.assertEqual([t.uuid for t in hits], [self.task.uuid])

    def test_bare_number_matches(self):
        for query in ("1", "#1"):
            hits = list(reference_tasks_qs(self.alice, query))
            self.assertEqual([t.uuid for t in hits], [self.task.uuid], query)

    def test_access_is_filtered(self):
        self.assertEqual(
            list(reference_tasks_qs(self.bob, f"{self.project.key}-1")), []
        )

    def test_archived_projects_are_excluded(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.assertEqual(
            list(reference_tasks_qs(self.alice, f"{self.project.key}-1")), []
        )

    def test_plain_text_query_matches_nothing(self):
        self.assertEqual(list(reference_tasks_qs(self.alice, "hero image")), [])

    def test_unknown_reference_matches_nothing(self):
        self.assertEqual(list(reference_tasks_qs(self.alice, "ZZZZ-999")), [])

    def test_search_module_puts_the_reference_hit_first(self):
        if not fts5_available():
            self.skipTest("FTS unavailable")
        results = search_project_tasks(f"{self.project.key}-1", self.alice, 10)
        self.assertEqual(results[0].uuid, str(self.task.uuid))
        self.assertIn(f"?task={self.task.uuid}", results[0].url)
        self.assertEqual(
            results[0].matched_value, f"{self.project.key}-{self.task.number}"
        )
