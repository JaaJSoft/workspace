from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workspace.files.models import File, ThumbnailFailure

User = get_user_model()


class FilesAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        cls.file = File.objects.create(
            owner=cls.admin, name="broken.jpg", node_type=File.NodeType.FILE
        )
        cls.failure = ThumbnailFailure.objects.create(
            file=cls.file, attempts=3, last_attempt_at=timezone.now()
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_file_change_list_renders(self):
        response = self.client.get(reverse("admin:files_file_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "broken.jpg")

    def test_retry_action_unparks_and_queues_generation(self):
        with patch("workspace.files.tasks.generate_thumbnails.delay") as delay:
            response = self.client.post(
                reverse("admin:files_thumbnailfailure_changelist"),
                {
                    "action": "retry_thumbnails",
                    "_selected_action": [str(self.failure.uuid)],
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ThumbnailFailure.objects.exists())
        delay.assert_called_once()

    def test_failure_rows_cannot_be_added_by_hand(self):
        self.assertEqual(
            self.client.get(reverse("admin:files_thumbnailfailure_add")).status_code,
            403,
        )
