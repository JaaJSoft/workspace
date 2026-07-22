from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from workspace.core.activity_registry import activity_registry
from workspace.projects.activity import ProjectsActivityProvider
from workspace.projects.models import TaskEvent
from workspace.projects.services.tasks import create_task
from workspace.projects.tests.base import ProjectTestMixin


class ProjectsActivityProviderTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.provider = ProjectsActivityProvider()
        self.task = create_task(self.project, self.admin, title="Visible work")

    def test_registered_in_global_registry(self):
        self.assertIn("projects", activity_registry.get_all())

    def test_recent_events_contract(self):
        events = self.provider.get_recent_events(self.admin.pk, limit=5)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["label"], "Task created")
        self.assertEqual(event["description"], "Visible work")
        self.assertEqual(event["icon"], "plus")
        self.assertEqual(event["url"], f"/projects/{self.project.uuid}")
        self.assertEqual(event["actor"]["username"], "admin1")
        self.assertIsNotNone(event["timestamp"])

    def test_member_viewer_sees_admins_events(self):
        events = self.provider.get_recent_events(
            self.admin.pk, viewer_id=self.member.pk
        )
        self.assertEqual(len(events), 1)

    def test_outsider_viewer_sees_nothing(self):
        events = self.provider.get_recent_events(
            self.admin.pk, viewer_id=self.outsider.pk
        )
        self.assertEqual(events, [])

    def test_user_filter_matches_actor(self):
        events = self.provider.get_recent_events(self.member.pk)
        self.assertEqual(events, [])

    def test_null_actor_event_is_not_misattributed(self):
        TaskEvent.objects.all().update(actor=None)
        events = self.provider.get_recent_events(None, viewer_id=self.admin.pk)
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["actor"])

    def test_daily_counts(self):
        today = timezone.now().date()
        counts = self.provider.get_daily_counts(
            self.admin.pk, today - timedelta(days=7), today
        )
        self.assertEqual(counts.get(today), 1)

    def test_stats(self):
        stats = self.provider.get_stats(self.admin.pk)
        self.assertEqual(stats["total_tasks"], 1)
        self.assertEqual(stats["completed_tasks"], 0)
