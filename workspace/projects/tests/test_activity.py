from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from workspace.core.activity_registry import activity_registry
from workspace.files.services import FileService
from workspace.projects.activity import ProjectsActivityProvider
from workspace.projects.models import TaskEvent
from workspace.projects.services.file_links import link_files
from workspace.projects.services.links import create_link
from workspace.projects.services.projects import create_project
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
        self.assertEqual(
            event["description"],
            f"{self.project.key}-{self.task.number} · Visible work",
        )
        self.assertEqual(event["icon"], "plus")
        self.assertEqual(
            event["url"], f"/projects/{self.project.uuid}?task={self.task.uuid}"
        )
        self.assertEqual(event["meta_icon"], "square-kanban")
        self.assertEqual(event["meta_text"], "Website")
        self.assertEqual(event["actor"]["username"], "admin1")
        self.assertIsNotNone(event["timestamp"])

    def test_recent_events_exclude_actor_before_the_limit(self):
        create_task(self.project, self.member, title="Member work")
        TaskEvent.objects.filter(actor=self.admin).update(
            created_at=timezone.now() + timedelta(hours=1)
        )

        events = self.provider.get_recent_events(
            None, limit=1, exclude_actor_id=self.admin.pk
        )

        self.assertEqual(
            [e["actor"]["username"] for e in events], [self.member.username]
        )

    def test_deleted_task_event_falls_back_to_project_url(self):
        expected = f"{self.project.key}-{self.task.number} · {self.task.title}"
        # Raw delete: SET_NULL keeps the created event but drops its task FK.
        self.task.delete()
        events = self.provider.get_recent_events(self.admin.pk, limit=5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["url"], f"/projects/{self.project.uuid}")
        self.assertEqual(events[0]["description"], expected)

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

    def test_file_event_names_the_file(self):
        doc = FileService.create_file(
            self.admin,
            "spec.md",
            content=SimpleUploadedFile("spec.md", b"# spec", content_type="text/plain"),
        )
        link_files(self.admin, self.task, [doc])
        seen_by_member = self.provider.get_recent_events(
            self.admin.pk, limit=5, viewer_id=self.member.pk
        )
        self.assertEqual(seen_by_member[0]["label"], "File linked: spec.md")


class HiddenLinkEventActivityTests(ProjectTestMixin, TestCase):
    """The admin links a Website task to a task of a project the member is
    not in: the member's feeds must not name that task."""

    def setUp(self):
        super().setUp()
        self.provider = ProjectsActivityProvider()
        self.task = create_task(self.project, self.admin, title="Visible work")
        secret = create_project(self.admin, name="Secret")
        self.hidden = create_task(secret, self.admin, title="Hidden")
        create_link(self.hidden, self.task, "blocks", actor=self.admin)

    def _names_hidden_task(self, events):
        return any(self.hidden.reference in event["label"] for event in events)

    def test_global_feed_drops_the_hidden_link_event(self):
        events = self.provider.get_recent_events(
            None, limit=50, viewer_id=self.member.pk
        )
        self.assertTrue(events)
        self.assertFalse(self._names_hidden_task(events))
        self.assertNotIn("Link added", [event["label"] for event in events])

    def test_profile_feed_drops_the_hidden_link_event(self):
        events = self.provider.get_recent_events(
            self.admin.pk, limit=50, viewer_id=self.member.pk
        )
        self.assertFalse(self._names_hidden_task(events))

    def test_daily_counts_skip_the_hidden_link_event(self):
        today = timezone.now().date()
        counts = self.provider.get_daily_counts(
            self.admin.pk, today - timedelta(days=1), today, viewer_id=self.member.pk
        )
        # Only the Website task's creation; neither link event.
        self.assertEqual(counts.get(today), 1)

    def test_viewer_with_access_to_both_ends_sees_the_reference(self):
        events = self.provider.get_recent_events(
            None, limit=50, viewer_id=self.admin.pk
        )
        self.assertTrue(self._names_hidden_task(events))
