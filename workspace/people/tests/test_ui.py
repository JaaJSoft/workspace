from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workspace.core.module_registry import registry

User = get_user_model()


class PeopleModuleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )

    def test_module_is_registered(self):
        module = registry.get("people")
        self.assertIsNotNone(module)
        self.assertEqual(module.url, "/people")

    def test_index_requires_login(self):
        response = self.client.get(reverse("people_ui:index"))
        self.assertEqual(response.status_code, 302)

    def test_index_renders(self):
        self.client.force_login(self.user)
        response = self.client.get("/people")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "People")
