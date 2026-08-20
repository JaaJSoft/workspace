"""The UNFOLD callbacks: environment label, sidebar badges, health cards."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from workspace.ai.models import AITask
from workspace.calendar.models import Calendar
from workspace.calendar.models_external import ExternalCalendar
from workspace.core.services import admin_dashboard
from workspace.core.services.admin_dashboard import (
    dashboard_callback,
    environment_callback,
    external_calendar_error_count,
    failed_ai_task_count,
    failed_import_job_count,
    mail_sync_error_count,
    thumbnail_failure_count,
)
from workspace.files.models import File, ThumbnailFailure
from workspace.imports.models import ImportConnection, ImportJob
from workspace.mail.models import MailAccount

User = get_user_model()


class EnvironmentCallbackTests(TestCase):
    @override_settings(DEBUG=True)
    def test_debug_reads_as_development(self):
        self.assertEqual(environment_callback(None), ["Development", "info"])

    @override_settings(DEBUG=False)
    def test_production_reads_as_danger(self):
        self.assertEqual(environment_callback(None), ["Production", "danger"])


def _make_account(owner, email, **overrides):
    fields = {
        "owner": owner,
        "email": email,
        "imap_host": "imap.test",
        "smtp_host": "smtp.test",
        "username": email,
    }
    fields.update(overrides)
    return MailAccount.objects.create(**fields)


class HealthCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="health", password="pw")

    def test_mail_errors_count_active_accounts_only(self):
        _make_account(self.user, "ok@test.com", last_sync_at=timezone.now())
        _make_account(self.user, "bad@test.com", last_sync_error="boom")
        _make_account(
            self.user, "off@test.com", is_active=False, last_sync_error="boom"
        )
        self.assertEqual(mail_sync_error_count(None), 1)

    def test_badge_wrappers_hide_zero_counts(self):
        # Unfold renders any non-empty badge value, so 0 must become None.
        for badge in (
            admin_dashboard.mail_sync_error_badge,
            admin_dashboard.external_calendar_error_badge,
            admin_dashboard.failed_ai_task_badge,
            admin_dashboard.thumbnail_failure_badge,
            admin_dashboard.failed_import_job_badge,
        ):
            self.assertIsNone(badge(None))
        _make_account(self.user, "bad2@test.com", last_sync_error="boom")
        self.assertEqual(admin_dashboard.mail_sync_error_badge(None), 1)

    def test_external_calendar_errors_count_active_feeds_only(self):
        cal = Calendar.objects.create(name="Feed", owner=self.user)
        ExternalCalendar.objects.create(calendar=cal, url="https://a.test/a.ics")
        cal2 = Calendar.objects.create(name="Broken", owner=self.user)
        ExternalCalendar.objects.create(
            calendar=cal2, url="https://a.test/b.ics", last_error="410 Gone"
        )
        cal3 = Calendar.objects.create(name="Off", owner=self.user)
        ExternalCalendar.objects.create(
            calendar=cal3,
            url="https://a.test/c.ics",
            is_active=False,
            last_error="410 Gone",
        )
        self.assertEqual(external_calendar_error_count(None), 1)

    def test_failed_ai_tasks_window_is_on_the_failure_time(self):
        AITask.objects.create(
            owner=self.user,
            task_type="chat",
            status="failed",
            completed_at=timezone.now(),
        )
        AITask.objects.create(
            owner=self.user,
            task_type="chat",
            status="failed",
            completed_at=timezone.now() - timedelta(hours=25),
        )
        AITask.objects.create(
            owner=self.user,
            task_type="chat",
            status="completed",
            completed_at=timezone.now(),
        )
        self.assertEqual(failed_ai_task_count(None), 1)

    def test_thumbnail_failures_are_counted(self):
        f = File.objects.create(
            owner=self.user, name="broken.jpg", node_type=File.NodeType.FILE
        )
        ThumbnailFailure.objects.create(
            file=f, attempts=3, last_attempt_at=timezone.now()
        )
        self.assertEqual(thumbnail_failure_count(None), 1)

    def test_failed_import_jobs_window_is_on_the_failure_time(self):
        conn = ImportConnection.objects.create(
            owner=self.user, provider="webdav", label="NC"
        )
        ImportJob.objects.create(
            connection=conn,
            status=ImportJob.Status.FAILED,
            kinds=["files"],
            finished_at=timezone.now(),
        )
        ImportJob.objects.create(
            connection=conn,
            status=ImportJob.Status.FAILED,
            kinds=["files"],
            finished_at=timezone.now() - timedelta(hours=25),
        )
        self.assertEqual(failed_import_job_count(None), 1)


class DashboardCallbackTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )

    def test_callback_injects_health_cards_with_tones(self):
        _make_account(self.admin, "bad@test.com", last_sync_error="boom")
        request = RequestFactory().get("/admin/")
        request.user = self.admin

        context = dashboard_callback(request, {})

        cards = context["health_cards"]
        self.assertEqual(len(cards), 5)
        by_title = {card["title"]: card for card in cards}
        self.assertEqual(by_title["Mail sync errors"]["value"], 1)
        self.assertEqual(by_title["Mail sync errors"]["tone"], "danger")
        self.assertEqual(by_title["Parked thumbnails"]["value"], 0)
        self.assertEqual(by_title["Parked thumbnails"]["tone"], "success")

    def test_cards_link_to_the_error_filtered_change_lists(self):
        request = RequestFactory().get("/admin/")
        request.user = self.admin

        by_title = {
            card["title"]: card["url"]
            for card in dashboard_callback(request, {})["health_cards"]
        }
        self.assertIn("?sync=error&is_active__exact=1", by_title["Mail sync errors"])
        self.assertIn(
            "?sync=error&is_active__exact=1", by_title["Calendar sync errors"]
        )
        self.assertIn("?status__exact=failed", by_title["Failed AI tasks"])
        self.assertIn("?status__exact=failed", by_title["Failed imports"])

    def test_admin_index_renders_the_cards(self):
        self.client.force_login(self.admin)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "System health")
        self.assertContains(response, "Mail sync errors")
