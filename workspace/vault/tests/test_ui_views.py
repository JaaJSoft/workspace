from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from workspace.users.services.settings import set_setting
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


class OnboardingDialogOpeningTests(TestCase):
    """When the setup dialog opens itself.

    The application's welcome tour opens itself too, 400 ms after load, and
    lands in front - swallowing the clicks meant for this one. The tour
    introduces the application, so it goes first.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _page(self):
        return self.client.get(reverse("vault_ui:onboarding")).content.decode()

    def test_it_waits_for_the_tour_when_the_tour_is_still_pending(self):
        html = self._page()
        self.assertIn("addEventListener('close'", html)
        self.assertNotIn('x-init="$el.showModal()"', html)

    def test_it_opens_at_once_for_an_account_that_has_seen_the_tour(self):
        set_setting(self.user, "core", "onboarding_completed", True)
        html = self._page()
        self.assertIn('x-init="$el.showModal()"', html)
        self.assertNotIn("addEventListener('close'", html)
