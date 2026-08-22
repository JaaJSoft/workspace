from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workspace.vault.models import AccountIdentity

User = get_user_model()


class VaultCspTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.client.force_login(self.user)

    def _policy(self, url_name):
        """Asserted on the header the browser receives, never on the setting
        behind it: a policy that is configured and never emitted protects
        nothing, and the two drift silently."""
        response = self.client.get(reverse(url_name))
        return response.headers["Content-Security-Policy"]

    def test_the_onboarding_view_emits_the_policy(self):
        policy = self._policy("vault_ui:onboarding")
        self.assertIn("default-src 'none'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("base-uri 'none'", policy)

    def test_the_breach_api_origin_is_reachable(self):
        """The strength floor queries api.pwnedpasswords.com under
        k-anonymity; connect-src 'self' alone would kill the feature and the
        page would look merely slow."""
        self.assertIn(
            "https://api.pwnedpasswords.com", self._policy("vault_ui:onboarding")
        )

    def test_alpine_can_still_evaluate_its_expressions(self):
        """Alpine 3 builds its expressions with new AsyncFunction(); without
        unsafe-eval every x-on: in the module stops firing."""
        self.assertIn("'unsafe-eval'", self._policy("vault_ui:onboarding"))

    def test_the_vault_list_carries_the_same_policy(self):
        AccountIdentity.objects.create(
            user=self.user, kdf_salt="SALT", state=AccountIdentity.State.ACTIVE
        )
        self.assertIn("default-src 'none'", self._policy("vault_ui:index"))

    def test_a_view_outside_the_module_is_left_alone(self):
        """The policy is per view on purpose: inline scripts and inline style
        attributes live elsewhere in the project, and a global policy is a
        separate piece of work."""
        response = self.client.get("/")
        self.assertNotIn("Content-Security-Policy", response.headers)
