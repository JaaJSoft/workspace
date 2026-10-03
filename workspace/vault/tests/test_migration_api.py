from django.test import TestCase
from django.utils import timezone

from workspace.vault.models import EntryType, VaultEntry
from workspace.vault.tests.factories import (
    HPKE_SUITE,
    make_account,
    make_key_wrap,
    make_vault,
    sealed,
)

CURRENT_HPKE = {**HPKE_SUITE, "format": 2}
LIST_URL = "/api/v1/vault/migration"


class MigrationListTests(TestCase):
    def setUp(self):
        self.user, self.signer, self.identity = make_account("owner")
        self.client.force_login(self.user)
        self.vault = make_vault(self.user)
        make_key_wrap(self.vault, self.user, hpke_suite=CURRENT_HPKE)

    def test_a_clean_account_answers_an_empty_list(self):
        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"vaults": []})

    def test_lists_a_trashed_stale_entry(self):
        entry = VaultEntry.objects.create(
            vault=self.vault,
            type=EntryType.LOGIN,
            encrypted_name=sealed("n", 1),
            metadata_sig="AQ",
        )
        VaultEntry.objects.filter(pk=entry.pk).update(deleted_at=timezone.now())
        [listed] = self.client.get(LIST_URL).json()["vaults"]
        self.assertEqual(listed["entries"], [str(entry.uuid)])

    def test_no_identity_is_404(self):
        self.identity.delete()
        self.assertEqual(self.client.get(LIST_URL).status_code, 404)

    def test_anonymous_is_refused(self):
        self.client.logout()
        self.assertIn(self.client.get(LIST_URL).status_code, (302, 401, 403))

    def test_response_is_not_cached(self):
        self.assertIn("no-store", self.client.get(LIST_URL)["Cache-Control"])
