from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workspace.ai.models import AITask

User = get_user_model()


class AIAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        cls.task = AITask.objects.create(
            owner=cls.admin, task_type="chat", status=AITask.Status.FAILED
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_task_change_list_renders_status_badge(self):
        response = self.client.get(reverse("admin:ai_aitask_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "failed")

    def test_conversation_summaries_cannot_be_added_by_hand(self):
        self.assertEqual(
            self.client.get(reverse("admin:ai_conversationsummary_add")).status_code,
            403,
        )
