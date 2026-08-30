from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from workspace.users.services.settings import set_setting
from workspace.vault.models import AccountIdentity, EntryType
from workspace.vault.tests.factories import make_vault

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


class BrowserRoutingTests(TestCase):
    # set_setting caches for five minutes in a process-global LocMemCache that
    # is not reset between TestCase runs.

    """`/vault` and `/vault/<uuid>` are one view.

    A palette command is a plain link, so it can name no UUID: the two URLs
    have to share a view for `?action=new` to reach a page able to honour it.
    The uuid path converter validates at routing time, so nothing in the view
    parses one.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")
        self.client.force_login(self.user)
        AccountIdentity.objects.create(
            user=self.user, kdf_salt="SALT", state=AccountIdentity.State.ACTIVE
        )

    def tearDown(self):
        cache.clear()

    def test_the_browser_route_renders_the_same_template(self):
        vault = make_vault(self.user)
        response = self.client.get(reverse("vault_ui:vault", args=[vault.uuid]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "vault/ui/index.html")

    def test_a_vault_out_of_reach_is_still_answered_by_the_page(self):
        """Never a 404, for the same reason the action endpoint never answers
        one: a 404 here would say the vault exists in somebody else's
        account. The page loads and finds nothing it can open."""
        stranger = User.objects.create_user(username="stranger", password="pw")
        response = self.client.get(
            reverse("vault_ui:vault", args=[make_vault(stranger).uuid])
        )
        self.assertEqual(response.status_code, 200)

    def test_the_page_is_handed_the_vault_it_was_asked_for(self):
        vault = make_vault(self.user)
        response = self.client.get(reverse("vault_ui:vault", args=[vault.uuid]))
        self.assertEqual(str(response.context["vault_uuid"]), str(vault.uuid))

    def test_the_listing_is_handed_no_vault(self):
        response = self.client.get(reverse("vault_ui:index"))
        self.assertIsNone(response.context["vault_uuid"])

    def test_a_malformed_uuid_never_reaches_the_view(self):
        """The path converter refuses it at routing time, which is why the
        view carries no parse_uuid_or_none."""
        self.assertEqual(self.client.get("/vault/not-a-uuid").status_code, 404)

    def test_the_page_carries_the_entry_type_catalogue(self):
        """The New menu and the entry form are built from the Python registry,
        so a type never has to be named twice."""
        response = self.client.get(reverse("vault_ui:index"))
        self.assertEqual(
            [entry["id"] for entry in response.context["entry_types"]],
            [EntryType.LOGIN],
        )
        self.assertContains(response, 'id="entry-types"')

    def test_the_listing_carries_the_account_preferences(self):
        """The lock delay has to reach the page: read only at unlock, a stored
        preference would not take effect until the next reload."""
        set_setting(self.user, "vault", "lock_after_minutes", 15)
        response = self.client.get(reverse("vault_ui:index"))
        self.assertEqual(response.context["vault_prefs"]["lock_after_minutes"], 15)
        self.assertContains(response, 'id="vault-prefs"')

    def test_an_account_with_no_preferences_gets_an_empty_map(self):
        response = self.client.get(reverse("vault_ui:index"))
        self.assertEqual(response.context["vault_prefs"], {})

    def test_an_unfinished_account_is_still_sent_to_onboarding(self):
        """The redirect guard belongs to the view, so it must hold on both
        routes rather than only on the one that had it."""
        AccountIdentity.objects.filter(user=self.user).update(
            state=AccountIdentity.State.PENDING
        )
        response = self.client.get(
            reverse("vault_ui:vault", args=[make_vault(self.user).uuid])
        )
        self.assertRedirects(response, reverse("vault_ui:onboarding"))
