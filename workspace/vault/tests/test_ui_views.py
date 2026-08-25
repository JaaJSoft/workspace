from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workspace.vault.models import AccountIdentity

User = get_user_model()


class OnboardingRoutingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.client.force_login(self.user)

    def test_a_user_without_an_identity_is_sent_to_onboarding(self):
        response = self.client.get(reverse("vault_ui:index"))
        self.assertRedirects(response, reverse("vault_ui:onboarding"))

    def test_a_pending_identity_still_means_onboarding(self):
        """init created the row and the browser never came back with the
        sealed keys: the account can open nothing, so the flow is unfinished
        and the user has to walk it again."""
        AccountIdentity.objects.create(user=self.user, kdf_salt="SALT")
        response = self.client.get(reverse("vault_ui:index"))
        self.assertRedirects(response, reverse("vault_ui:onboarding"))

    def test_an_active_identity_reaches_the_vault_list(self):
        AccountIdentity.objects.create(
            user=self.user, kdf_salt="SALT", state=AccountIdentity.State.ACTIVE
        )
        response = self.client.get(reverse("vault_ui:index"))
        self.assertEqual(response.status_code, 200)

    def test_onboarding_sends_a_finished_account_back_to_the_vault(self):
        """Walking it twice would mint a new salt, and the sealed private keys
        are the only path back to every VaultKeyWrap the account holds."""
        AccountIdentity.objects.create(
            user=self.user, kdf_salt="SALT", state=AccountIdentity.State.ACTIVE
        )
        response = self.client.get(reverse("vault_ui:onboarding"))
        self.assertRedirects(response, reverse("vault_ui:index"))

    def test_another_users_identity_does_not_open_the_vault(self):
        other = User.objects.create_user(username="other", password="pw")
        AccountIdentity.objects.create(
            user=other, kdf_salt="SALT", state=AccountIdentity.State.ACTIVE
        )
        response = self.client.get(reverse("vault_ui:index"))
        self.assertRedirects(response, reverse("vault_ui:onboarding"))

    def test_both_views_require_authentication(self):
        self.client.logout()
        for name in ("vault_ui:index", "vault_ui:onboarding"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response["Location"])
