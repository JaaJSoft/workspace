"""The rehearsals of an algorithm replacement, in a real browser.

Rehearsal 1: a format change lands, and the next unlock moves every row.

Format 1 has been superseded since the format-2 release, so this runs the
production code as it ships: rows the reference wrote under format 1 must be
under format 2 after one unlock, still verifying, with nothing left listed -
and only for the account that unlocked, only for rows it could verify.

The format-1 rows are written straight into the database: the API refuses to
store a ciphertext under a superseded format, so only the database can stand
in for what an older build left behind.

Rehearsal 2: AES-256-GCM is superseded by the test AEAD, through the test
bundle, and the next unlock reseals every row under it.
"""

import hashlib
from collections import Counter

from django.contrib.auth import get_user_model
from django.test import override_settings
from playwright.sync_api import Error as PlaywrightError

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.vault.models import (
    AccountIdentity,
    Vault,
    VaultEntry,
    VaultKeyWrap,
    VaultTag,
)
from workspace.vault.queries import user_vault_ids
from workspace.vault.services.census import census, ciphertext_marks, stale_rows
from workspace.vault.tests.reference.encoding import from_base64url, to_base64url

from .. import compat
from .compat_scripts import READ_EVERYTHING
from .reference_rows import ReferenceRowsMixin
from .test_browser import CORPUS_ROUTE, PANEL, VaultBrowserCase
from .test_compat_browser import VERIFY_EVERY_SIGNATURE

TAMPERED_BANNER = "inline-alert:has-text('removed from the list')"
# AES-256-GCM superseded by the test AEAD: the switch a real algorithm
# replacement would ship as a manifest change.
AEAD_SWITCH = {"aead": {"1": "superseded", "240": "current"}}
# One more than a migrate batch holds, so a pass takes two requests and can be
# cut between them.
MORE_THAN_A_BATCH = 201


def _entry_format(entry_uuid):
    return from_base64url(VaultEntry.objects.get(uuid=entry_uuid).encrypted_name)[0]


def _stored_rows(user):
    """Every column a migration could rewrite on the user's rows, and the
    timestamp it must not move."""
    vault_ids = list(user_vault_ids(user))
    return {
        "vaults": list(
            Vault.objects.filter(uuid__in=vault_ids)
            .order_by("uuid")
            .values_list(
                "encrypted_name", "encrypted_description", "metadata_sig", "updated_at"
            )
        ),
        "wraps": list(
            VaultKeyWrap.objects.filter(recipient=user)
            .order_by("vault_id")
            .values_list("wrapped_key", "hpke_suite")
        ),
        "entries": list(
            VaultEntry.objects.filter(vault_id__in=vault_ids)
            .order_by("uuid")
            .values_list(
                "encrypted_name", "encrypted_notes", "metadata_sig", "updated_at"
            )
        ),
    }


class FormatMigrationBrowserTests(ReferenceRowsMixin, VaultBrowserCase):
    def _seed_format_1(self):
        self._open_vault()
        self._write_entries_as_reference(["Old one", "Old two"], format_version=1)

    def _reload_and_unlock(self):
        self.page.reload()
        self._unlock()

    def test_one_unlock_moves_every_row_to_format_2(self):
        self._seed_format_1()
        vault = Vault.objects.get(uuid=self.vault_uuid)
        self.assertIn(1, self._formats(vault))
        self.assertNotEqual(stale_rows(self.user), [])

        self._reload_and_unlock()
        self._wait_for_no_migration()

        self.assertEqual(self._formats(vault), {2})
        self.assertEqual(stale_rows(self.user), [])
        # The page only lists a row whose signature verified, and the
        # migration re-signed both: a signature it got wrong would drop the
        # row from the listing and raise the tampered banner.
        self._reload_and_unlock()
        self.page.wait_for_selector("tbody tr:has-text('Old two')", timeout=30000)
        self.page.wait_for_selector("tbody tr:has-text('Old one')", timeout=30000)
        self.assertEqual(self.page.locator(TAMPERED_BANNER).count(), 0)

    def test_nothing_changes_before_an_unlock(self):
        self._seed_format_1()
        listings = []
        self.page.on(
            "request",
            lambda request: (
                listings.append(request.url)
                if "/api/v1/vault/migration" in request.url
                or request.url.endswith("/migrate")
                else None
            ),
        )
        before = _stored_rows(self.user)

        self.page.reload()
        self.page.wait_for_selector("input[autocomplete='current-password']")
        # Long enough for a migration that started without an unlock to have
        # asked for its listing - it does within a second of an unlock.
        self.page.wait_for_timeout(3000)

        self.assertEqual(listings, [])
        self.assertEqual(_stored_rows(self.user), before)

    def test_an_interrupted_migration_is_finished_by_the_next_unlock(self):
        """Cut for real: more stale rows than one request carries, the first
        request let through and the second dropped on the wire, so the first
        unlock leaves the vault half migrated."""
        self._open_vault()
        names = [f"Old {index:03d}" for index in range(MORE_THAN_A_BATCH)]
        self._write_entries_as_reference(names, format_version=1)
        vault = Vault.objects.get(uuid=self.vault_uuid)
        posts = []

        def cut_after_first(route):
            posts.append(route.request.url)
            if len(posts) > 1:
                route.abort()
            else:
                route.continue_()

        self.page.route("**/migrate", cut_after_first)
        self._reload_and_unlock()
        for _ in range(600):
            if len(posts) >= 2:
                break
            self.page.wait_for_timeout(100)
        self.assertEqual(len(posts), 2, "the pass never sent its second batch")
        self.page.unroute("**/migrate")

        entry_formats = Counter(
            from_base64url(name)[0]
            for name in VaultEntry.objects.filter(vault=vault).values_list(
                "encrypted_name", flat=True
            )
        )
        self.assertEqual(entry_formats, Counter({2: 200, 1: 1}))
        self.assertNotEqual(stale_rows(self.user), [])
        self._assert_opens_entirely(names)

        self._reload_and_unlock()
        self._wait_for_no_migration(timeout_ms=60000)
        self.assertEqual(self._formats(vault), {2})
        self.assertEqual(stale_rows(self.user), [])

    def _assert_opens_entirely(self, names):
        """A half-migrated vault is an ordinary vault: every row, whichever
        format it is under, opens and verifies. The migration listing is
        answered empty so that this unlock reads the vault as the cut left it."""
        self.page.route(
            "**/api/v1/vault/migration",
            lambda route: route.fulfill(json={"vaults": []}),
        )
        self._reload_and_unlock()
        self.page.wait_for_selector("tbody tr", timeout=30000)
        read = self.page.evaluate(
            """async (vaultUuid) => {
              const A = window.vaultApi, S = window.vaultSession;
              const vault = (await A.listVaults()).find((row) => row.uuid === vaultUuid);
              const rows = [
                ...(await A.listEntries(vaultUuid)),
                ...(await A.listEntries(vaultUuid, { trashed: true })),
              ];
              const opened = await window.vaultReader.readVault(S, vault);
              const read = await window.vaultReader.readEntries(S, opened, rows);
              return {
                listed: rows.length,
                names: read.rows.map((row) => row.name).sort(),
                tampered: read.tamperedCount,
                unsupported: read.unsupportedCount,
              };
            }""",
            self.vault_uuid,
        )
        self.page.unroute("**/api/v1/vault/migration")
        self.assertEqual(read["listed"], MORE_THAN_A_BATCH)
        self.assertEqual(read["names"], sorted(names))
        self.assertEqual((read["tampered"], read["unsupported"]), (0, 0))
        self.assertEqual(self.page.locator(TAMPERED_BANNER).count(), 0)

    def test_a_tampered_row_is_left_alone(self):
        self._seed_format_1()
        tampered, untouched = VaultEntry.objects.filter(
            vault__uuid=self.vault_uuid
        ).order_by("uuid")
        sig = bytearray(from_base64url(tampered.metadata_sig))
        sig[10] ^= 0xFF
        forged = to_base64url(bytes(sig))
        VaultEntry.objects.filter(pk=tampered.pk).update(metadata_sig=forged)

        self._reload_and_unlock()
        self.page.wait_for_selector(TAMPERED_BANNER, timeout=30000)
        # Both rows sit in the same vault and the same batch, so the moment
        # the verified one is rewritten the pass has decided the forged one.
        for _ in range(300):
            if _entry_format(untouched.uuid) == 2:
                break
            self.page.wait_for_timeout(100)
        self.assertEqual(_entry_format(untouched.uuid), 2)

        stored = VaultEntry.objects.get(pk=tampered.pk)
        self.assertEqual(stored.encrypted_name, tampered.encrypted_name)
        self.assertEqual(stored.metadata_sig, forged)
        self.assertEqual(from_base64url(stored.encrypted_name)[0], 1)
        self.assertEqual(
            [item["entries"] for item in stale_rows(self.user)], [[str(tampered.uuid)]]
        )

    def test_a_never_unlocked_account_waits_for_its_own_unlock(self):
        owner = self.user
        owner_context, owner_page = self.context, self.page
        # The second account gets a browser of its own: onboarding remembers
        # the device key in the browser's storage, and one profile shared by
        # two accounts would test the storage, not the migration.
        second = self.create_user(username="second", email="second@example.com")
        second_context = self.browser.new_context()
        self.addCleanup(second_context.close)
        second_page = second_context.new_page()
        second_context.route(
            "**/api/v1/stream**", lambda route: route.fulfill(status=204, body="")
        )
        second_page.route(
            CORPUS_ROUTE,
            lambda route: route.fulfill(
                status=200, body="0000000000000000000000000000000000000:1\n"
            ),
        )

        def act_as(user, context, page):
            self.user, self.context, self.page = user, context, page

        # tearDown closes self.context: it must be the owner's again by then.
        self.addCleanup(act_as, owner, owner_context, owner_page)

        act_as(second, second_context, second_page)
        self.login_as(second)
        self._seed_format_1()
        second_vault = Vault.objects.get(uuid=self.vault_uuid)
        # Locked, the way a closed tab is: nothing of this account runs while
        # the owner unlocks.
        self.page.goto("about:blank")

        act_as(owner, owner_context, owner_page)
        self._seed_format_1()
        owner_vault = Vault.objects.get(uuid=self.vault_uuid)
        second_before = _stored_rows(second)
        self.assertNotEqual(stale_rows(second), [])

        self._reload_and_unlock()
        self._wait_for_no_migration()
        self.assertEqual(self._formats(owner_vault), {2})
        self.assertEqual(_stored_rows(second), second_before)
        self.assertNotEqual(stale_rows(second), [])

        act_as(second, second_context, second_page)
        self.page.goto(f"{self.live_server_url}/vault/{second_vault.uuid}")
        self._unlock()
        self._wait_for_no_migration()
        self.assertEqual(self._formats(second_vault), {2})
        self.assertEqual(stale_rows(second), [])


@override_settings(VAULT_TEST_MANIFEST=AEAD_SWITCH)
class AeadMigrationBrowserTests(ReferenceRowsMixin, VaultBrowserCase):
    """Rehearsal 2: an AEAD replacement lands, and the next unlock moves every
    row to the new algorithm.

    The page that onboards and writes the first rows runs before the switch,
    on the production bundle; the reload after it gets the test bundle with
    the overrides installed, the way a deployment would hand a new manifest
    to a browser that last ran the old one.
    """

    def test_one_unlock_moves_every_row_to_the_test_aead(self):
        with override_settings(VAULT_TEST_MANIFEST=None):
            self._open_vault()
            self._create_entry("Before", "octocat", "hunter2")
        vault = Vault.objects.get(uuid=self.vault_uuid)
        self.assertEqual(self._aeads(vault), {1})
        # Under the switch the server already counts them as stale.
        self.assertNotEqual(stale_rows(self.user), [])

        self.page.reload()
        self._unlock()
        self._wait_for_no_migration()

        self.assertEqual(self._aeads(vault), {240})
        self.assertEqual(self._formats(vault), {2})
        self.assertEqual(stale_rows(self.user), [])
        # Re-signed along the way: a signature the migration got wrong would
        # drop the row from the listing and raise the tampered banner.
        self.page.reload()
        self._unlock()
        self.page.wait_for_selector("tbody tr:has-text('Before')", timeout=30000)
        self.assertEqual(self.page.locator(TAMPERED_BANNER).count(), 0)


CORPUS = compat.load("v1")


def _corpus_digests():
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(CORPUS.root.iterdir())
        if path.is_file()
    }


class UnverifiedCarrierBrowserTests(VaultBrowserCase):
    def test_a_tag_on_a_tampered_entry_is_not_deleted(self):
        """The entry's signature is inside what a tag removal re-signs, so a
        carrier the page could not verify blocks the removal before any
        request goes out, and the row is left byte for byte as it was."""
        self._open_vault()
        self.page.get_by_role("button", name="New tag").click()
        self.page.fill(".modal-box input[type=text]", "Banking")
        self.page.click(".modal-box button:has-text('Create')")
        self.page.wait_for_selector("aside tag-chip", timeout=30000)
        self._create_entry("GitHub", "octocat", "hunter2")
        self.page.click("tbody tr:has-text('GitHub')")
        self.page.click(f"{PANEL} button:has-text('Edit')")
        self.page.locator(".modal-box").get_by_text("Banking").click()
        self.page.click(".modal-box button:has-text('Save')")
        self.page.wait_for_selector(
            "tbody tr:has-text('GitHub') tag-chip", timeout=30000
        )

        entry = VaultEntry.objects.get(vault__uuid=self.vault_uuid)
        signature = from_base64url(entry.metadata_sig)
        forged = to_base64url(signature[:1] + bytes(len(signature) - 1))
        VaultEntry.objects.filter(pk=entry.pk).update(metadata_sig=forged)
        tag = VaultTag.objects.get()
        deletions = []
        self.page.on(
            "request",
            lambda request: (
                deletions.append(request.url)
                if request.url.endswith("/delete")
                else None
            ),
        )

        self.page.reload()
        self._unlock()
        self.page.wait_for_selector(TAMPERED_BANNER, timeout=30000)
        self.page.locator("button[aria-label='Delete the tag Banking']").click(
            force=True
        )
        self.page.locator(".modal-box button:has-text('Delete')").click()
        self.page.wait_for_selector(
            "text=This tag is on an entry that could not be verified", timeout=30000
        )
        self.page.wait_for_timeout(1000)

        self.assertEqual(deletions, [])
        self.assertTrue(VaultTag.objects.filter(pk=tag.pk).exists())
        stored = VaultEntry.objects.get(pk=entry.pk)
        self.assertEqual(stored.metadata_sig, forged)
        self.assertEqual(list(stored.tags.values_list("pk", flat=True)), [tag.pk])


class FrozenAccountMigrationBrowserTests(ReferenceRowsMixin, PlaywrightTestCase):
    """The account the frozen `v1/` corpus holds, unlocked by today's page.

    The corpus files are the record of what format 1 wrote and must never
    change; the rows loaded from them into the test database are what this
    test expects the page to rewrite.
    """

    fixtures = [str(CORPUS.rows)]

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.get(
            username=CORPUS.credentials["username"]
        )
        self.login_as(self.user)
        self.page.route(
            CORPUS_ROUTE,
            lambda route: route.fulfill(
                status=200, body="0000000000000000000000000000000000000:1\n"
            ),
        )

    def _unlock(self):
        self.page.goto(f"{self.live_server_url}/vault")
        self.page.wait_for_selector("input[autocomplete='current-password']")
        self.page.fill(
            "input[autocomplete='current-password']",
            CORPUS.credentials["vault_master_password"],
        )
        self.page.fill("input[spellcheck='false']", CORPUS.credentials["secret_key"])
        self.page.click("button:has-text('Unlock')")
        self.page.wait_for_function(
            "() => window.vaultSession && window.vaultSession.isUnlocked()",
            timeout=60000,
        )

    def test_the_frozen_format_1_account_migrates(self):
        digests = _corpus_digests()
        self.assertNotEqual(stale_rows(self.user), [])
        updated_before = dict(VaultEntry.objects.values_list("uuid", "updated_at"))

        self._unlock()
        self._wait_for_no_migration(timeout_ms=60000)

        self.assertEqual(stale_rows(self.user), [])
        self.assertEqual(
            dict(VaultEntry.objects.values_list("uuid", "updated_at")), updated_before
        )
        # Rewritten, and still the same account: every plaintext the corpus
        # recorded opens from the new ciphertexts, and every row verifies.
        try:
            read = self.page.evaluate(READ_EVERYTHING)
            counts = self.page.evaluate(VERIFY_EVERY_SIGNATURE)
        except PlaywrightError as exc:
            raise AssertionError(
                f"the migrated corpus account no longer opens: {exc}"
            ) from exc
        self.assertEqual(read, CORPUS.manifest)
        manifest_vaults = CORPUS.manifest["vaults"]
        self.assertEqual(
            counts["entries"], sum(len(v["entries"]) for v in manifest_vaults)
        )

        # The account envelope is not this migration's to move: format 1
        # survives on the identity's two wrapped keys and nowhere else.
        identity_marks = Counter()
        for kex, sig in AccountIdentity.objects.values_list(
            "wrapped_kex_priv", "wrapped_sig_priv"
        ):
            identity_marks.update(ciphertext_marks(kex) + ciphertext_marks(sig))
        counts_by_mark = census()
        self.assertGreater(identity_marks[("format", 1)], 0)
        self.assertEqual(counts_by_mark[("format", 1)], identity_marks[("format", 1)])
        self.assertEqual(counts_by_mark[("hpke", 1)], 0)

        self.assertEqual(_corpus_digests(), digests)
