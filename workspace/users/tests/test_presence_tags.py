from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.users.services.presence import set_manual_status
from workspace.users.templatetags.presence_tags import manual_status

User = get_user_model()


class ManualStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="presence_user", password="pw")

    def tearDown(self):
        cache.clear()

    def test_a_user_who_picked_none_is_auto(self):
        self.assertEqual(manual_status(self.user), "auto")

    def test_the_user_menu_opens_on_the_picked_status_without_a_request(self):
        set_manual_status(self.user.pk, "busy")
        self.client.force_login(self.user)

        resp = self.client.get("/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "current: 'busy',")
