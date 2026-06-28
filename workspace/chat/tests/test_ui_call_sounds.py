from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from workspace.users.services.settings import set_setting


class ChatPageCallSoundsContextTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="u", password="x")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_call_sounds_enabled_by_default(self):
        resp = self.client.get(reverse("chat_ui:index"))
        self.assertTrue(resp.context["call_sounds_enabled"])

    def test_call_sounds_reflects_setting(self):
        set_setting(self.user, "chat", "call_sounds", False)
        resp = self.client.get(reverse("chat_ui:index"))
        self.assertFalse(resp.context["call_sounds_enabled"])
