from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.files.models import File, FileShareLink
from workspace.files.services.public_links import (
    schedule_upload_notification,
    upload_notification_cache_key,
)
from workspace.files.tasks import notify_share_link_uploads
from workspace.notifications.models import Notification

User = get_user_model()


class UploadNotificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username="owner", email="owner@example.com", password="pass123"
        )
        self.folder = File.objects.create(
            owner=self.owner, name="Drop", node_type=File.NodeType.FOLDER
        )
        self.link = FileShareLink.objects.create(
            file=self.folder, created_by=self.owner, mode=FileShareLink.Mode.DROP
        )

    def tearDown(self):
        cache.clear()

    def test_only_the_first_upload_of_a_burst_schedules_a_task(self):
        with patch(
            "workspace.files.tasks.notify_share_link_uploads.apply_async"
        ) as scheduled:
            schedule_upload_notification(self.link)
            schedule_upload_notification(self.link)
            schedule_upload_notification(self.link)
        self.assertEqual(scheduled.call_count, 1)

    def test_the_task_sends_one_notification_for_the_whole_burst(self):
        FileShareLink.objects.filter(pk=self.link.pk).update(upload_count=4)
        notify_share_link_uploads(str(self.link.uuid))
        notification = Notification.objects.get(recipient=self.owner)
        self.assertIn("4 files", notification.title)
        self.assertIn("Drop", notification.title)
        self.assertIsNone(notification.actor)

    def test_a_single_upload_reads_as_singular(self):
        FileShareLink.objects.filter(pk=self.link.pk).update(upload_count=1)
        notify_share_link_uploads(str(self.link.uuid))
        self.assertIn(
            "1 file was", Notification.objects.get(recipient=self.owner).title
        )

    def test_the_task_advances_the_high_water_mark(self):
        FileShareLink.objects.filter(pk=self.link.pk).update(upload_count=4)
        notify_share_link_uploads(str(self.link.uuid))
        self.link.refresh_from_db()
        self.assertEqual(self.link.notified_upload_count, 4)

    def test_a_second_run_with_no_new_uploads_notifies_nothing(self):
        FileShareLink.objects.filter(pk=self.link.pk).update(upload_count=2)
        notify_share_link_uploads(str(self.link.uuid))
        notify_share_link_uploads(str(self.link.uuid))
        self.assertEqual(Notification.objects.filter(recipient=self.owner).count(), 1)

    def test_the_task_clears_the_guard_so_the_next_burst_schedules(self):
        # Patched so the assertion below is about scheduling having claimed the
        # guard, not about whether this environment runs Celery eagerly.
        with patch("workspace.files.tasks.notify_share_link_uploads.apply_async"):
            schedule_upload_notification(self.link)
        self.assertIsNotNone(cache.get(upload_notification_cache_key(self.link.uuid)))

        notify_share_link_uploads(str(self.link.uuid))
        self.assertIsNone(cache.get(upload_notification_cache_key(self.link.uuid)))

    def test_a_deleted_link_does_not_raise(self):
        uuid = str(self.link.uuid)
        self.link.delete()
        notify_share_link_uploads(uuid)
        self.assertFalse(Notification.objects.exists())

    def test_a_broker_failure_does_not_break_the_caller(self):
        """The bytes are stored already: a lost notification beats a lost file."""
        with patch(
            "workspace.files.tasks.notify_share_link_uploads.apply_async",
            side_effect=OSError("broker unreachable"),
        ):
            schedule_upload_notification(self.link)
        # The guard is released, so the next upload can try again.
        self.assertIsNone(cache.get(upload_notification_cache_key(self.link.uuid)))
