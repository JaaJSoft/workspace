from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

User = get_user_model()


class VaultIndexViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get("/vault")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_renders_for_an_authenticated_user(self):
        self.client.force_login(self.user)
        response = self.client.get("/vault")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "vault/ui/index.html")

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_preview_hiding_does_not_gate_the_request(self):
        """The preview flag hides the module from the navigation, the module
        grid and the command palette - it is not request-level enforcement.

        This asserts the known limitation rather than the desired end state,
        so that adopting request-level access control fails here loudly and
        forces a deliberate update instead of silently widening the gate.
        """
        self.client.force_login(self.user)
        self.assertFalse(self.user.is_staff)
        self.assertEqual(self.client.get("/vault").status_code, 200)
