from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class AdminChangeListTests(TestCase):
    """The themed admin change lists must render under the installed Django."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        self.client.force_login(self.admin)

    def test_knox_token_change_list_renders(self):
        response = self.client.get("/admin/knox/authtoken/")
        self.assertEqual(response.status_code, 200)

    def test_user_change_list_renders_rows(self):
        response = self.client.get("/admin/auth/user/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "root@example.com")
