from io import BytesIO
from unittest.mock import patch

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse
from PIL import Image

from workspace.ai.admin import BotProfileAdmin, BotProfileForm
from workspace.ai.models import AITask, BotProfile
from workspace.users.services.avatar import has_avatar

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


class BotProfileAdminAvatarTests(TestCase):
    def setUp(self):
        self.bot_user = User.objects.create_user(username="botty", password="pw")

    def tearDown(self):
        cache.clear()

    @staticmethod
    def _png():
        buf = BytesIO()
        Image.new("RGB", (64, 64), color="blue").save(buf, format="PNG")
        return SimpleUploadedFile("avatar.png", buf.getvalue(), "image/png")

    @patch("workspace.users.services.avatar.save_image")
    def test_save_model_saves_the_uploaded_avatar(self, mock_save_image):
        form = BotProfileForm(
            data={"user": self.bot_user.pk}, files={"avatar": self._png()}
        )
        self.assertTrue(form.is_valid(), form.errors)

        model_admin = BotProfileAdmin(BotProfile, django_admin.site)
        request = RequestFactory().post("/admin/ai/botprofile/add/")
        model_admin.save_model(request, form.save(commit=False), form, change=False)

        mock_save_image.assert_called_once()
        self.assertTrue(has_avatar(self.bot_user))
