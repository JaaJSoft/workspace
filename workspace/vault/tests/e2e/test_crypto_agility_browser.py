"""The dispatch, proven where it runs: a real page reading rows it did not write.

The reference implementation writes rows under the test AEAD and under format 1;
the page, running the production bundle plus the test-suite script, must read
them beside its own format-2 rows. Then the negative half: bytes naming an
algorithm nobody implements read as "needs a newer version", never as a wrong
password or as tampering.

Rows are written straight into the database for the same reason the forged
signature in test_browser.py is: the API refuses a ciphertext naming an AEAD it
does not know, so only the database can stand in for a newer build's writes.
"""

from workspace.vault.models import AccountIdentity, VaultEntry
from workspace.vault.tests.reference import wire

from .reference_rows import ReferenceRowsMixin
from .test_browser import GOOD_PASSWORD, VaultBrowserCase

# :visible because the page also holds the no-openable-vault alerts, hidden
# with x-show, and one of them says the same words.
UNSUPPORTED_BANNER = "inline-alert:visible:has-text('a newer version of the app')"
TAMPERED_BANNER = "inline-alert:has-text('removed from the list')"
# An id no manifest declares, so no build of this app can open it.
UNKNOWN_AEAD_ID = 0x07


class CryptoAgilityBrowserTests(ReferenceRowsMixin, VaultBrowserCase):
    def test_rows_under_three_constructions_list_side_by_side(self):
        self._open_vault()
        self._write_entry_as_reference("Format 1", format_version=1)
        self._write_entry_as_reference("Test aead", aead_id=wire.AEAD_TEST_CTR_HMAC)
        self._create_entry("Format 2", "octocat", "hunter2")

        self._reload_and_unlock()
        # The listing renders once every row has been read, so the page's own
        # row appearing means the other two were decided too.
        self.page.wait_for_selector("tbody tr:has-text('Format 2')", timeout=30000)
        self.assertEqual(
            sorted(self._listed_entry_names()), ["Format 1", "Format 2", "Test aead"]
        )
        self.assertEqual(self._banner_count(TAMPERED_BANNER), 0)
        self.assertEqual(self._banner_count(UNSUPPORTED_BANNER), 0)

    def test_an_envelope_under_an_unknown_kdf_asks_for_a_reload(self):
        self._onboard()
        AccountIdentity.objects.filter(user=self.user).update(kdf_algo="scrypt")

        self.page.reload()
        self.page.wait_for_selector("input[autocomplete='current-password']")
        self.page.fill("input[autocomplete='current-password']", GOOD_PASSWORD)
        self.page.click("button:has-text('Unlock')")
        error = self.page.locator("inline-alert[type='error']:visible").first
        error.wait_for(timeout=60000)
        text = error.inner_text()
        self.assertIn("newer version of the app", text)
        # Both wordings of a refused password: the remembered-key variant is
        # the one this account would get, since onboarding ticked "remember".
        self.assertNotIn("does not open this account", text)
        self.assertNotIn("did not open this account", text)

    def test_a_row_under_an_unknown_aead_is_counted_as_needing_a_reload(self):
        self._open_vault()
        self._create_entry("From the future", "octocat", "hunter2")
        entry = VaultEntry.objects.get(vault__uuid=self.vault_uuid)
        self._rewrite_header_byte(entry, "encrypted_name", 1, UNKNOWN_AEAD_ID)

        self._reload_and_unlock()
        # Either banner: waiting on the right one alone would turn a row
        # misread as tampered into a timeout instead of a failed count.
        self.page.wait_for_selector(
            f"{UNSUPPORTED_BANNER}, {TAMPERED_BANNER}", timeout=30000
        )
        self.assertEqual(self._banner_count(UNSUPPORTED_BANNER), 1)
        self.assertEqual(self._banner_count(TAMPERED_BANNER), 0)
        self.assertEqual(self._listed_entry_names(), [])

    # ---- the page ----------------------------------------------------------

    def _reload_and_unlock(self):
        self.page.reload()
        self._unlock()
        self.page.wait_for_selector("text=All entries", timeout=30000)

    def _listed_entry_names(self):
        return self.page.locator(
            "[data-testid='entry-list'] tbody tr span.font-medium"
        ).all_inner_texts()

    def _banner_count(self, banner):
        """The number a banner states, 0 when it is not rendered at all."""
        found = self.page.locator(banner)
        if not found.count():
            return 0
        return int(found.first.locator("b > span").first.inner_text())
