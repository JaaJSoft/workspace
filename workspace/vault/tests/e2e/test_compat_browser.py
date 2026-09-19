"""A real browser opens an account this build never wrote.

Task 2 (test_compat_reference.py) and Task 3 (test_compat_server.py) both
prove the corpus, but both share ``cbor2`` between the writer and the
reader - the module's own docs call that agreement partly circular. The
browser bundle carries its own CBOR encoder (``cbor-x``), its own AEAD, its
own everything: it is the only reader here that shares no line with either
half of the corpus's own generation walk. Opening the frozen rows with it is
what closes the circularity.

Two scripts verify the frozen corpus: ``READ_EVERYTHING`` decrypts every
ciphertext and compares the manifest to ensure all bytes still open under
today's bundle, and ``VERIFY_EVERY_SIGNATURE`` checks every signed row
through the client's own verification path. Decryption proves ciphertexts
survive, signature verification proves the signing path still validates them,
and a canonical-encoding regression breaks only the second - hence both are
needed.
"""

from django.contrib.auth import get_user_model
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from workspace.common.tests.e2e.base import PlaywrightTestCase

from .. import compat
from .compat_scripts import READ_EVERYTHING

# Every published corpus version a replay test in this file reads. Task 5
# checks this against compat.versions() so a new corpus directory can never
# go unread.
COVERED = ["v1"]

CORPUS_ROUTE = "https://api.pwnedpasswords.com/range/*"

# READ_EVERYTHING only opens ciphertext - it never asks whether a signature
# still verifies, so a canonical-encoding regression that breaks every
# metadata_sig without touching a single AEAD byte would sail through it
# unnoticed. This script is the other half: every signed row of every vault,
# rebuilt from what the server serves (never from the manifest, which was
# never signed) and checked with the client's own verification path -
# window.vaultSession.verifyRecord, the same call site vault_reader.js makes
# before showing a row to a user. Not shared with the generation walk - it
# reads what generation already wrote, it does not decide what gets written -
# so it lives here rather than in compat_scripts.py.
VERIFY_EVERY_SIGNATURE = """
async () => {
  const V = window.vaultCrypto, A = window.vaultApi, S = window.vaultSession;
  const counts = { vaults: 0, folders: 0, tags: 0, entries: 0 };

  const describe = (kind, uuid, cause) =>
    new Error(`${kind} ${uuid} failed to verify: ${cause && cause.message}`);

  for (const vault of await A.listVaults()) {
    const vaultPayload = V.vaultMetadataPayload({
      vault_uuid: vault.uuid,
      owner_account_uuid: vault.owner_account_uuid,
      encrypted_name: vault.encrypted_name,
      encrypted_description: vault.encrypted_description,
      icon: vault.icon,
      color: vault.color,
      key_version: vault.key_version,
      is_favorite: vault.is_favorite,
    });
    try {
      await S.verifyRecord(vaultPayload, vault.metadata_sig, V.VAULT_METADATA_TYPE);
    } catch (cause) {
      throw describe('vault', vault.uuid, cause);
    }
    counts.vaults += 1;

    for (const folder of await A.listFolders(vault.uuid)) {
      const folderPayload = V.folderMetadataPayload({
        folder_uuid: folder.uuid,
        vault_uuid: folder.vault,
        signer_account_uuid: S.accountUuid(),
        parent_uuid: folder.parent,
        position: folder.position,
        encrypted_name: folder.encrypted_name,
      });
      try {
        await S.verifyRecord(folderPayload, folder.metadata_sig, V.FOLDER_METADATA_TYPE);
      } catch (cause) {
        throw describe('folder', folder.uuid, cause);
      }
      counts.folders += 1;
    }

    for (const tag of await A.listTags(vault.uuid)) {
      const tagPayload = V.tagMetadataPayload({
        tag_uuid: tag.uuid,
        vault_uuid: tag.vault,
        signer_account_uuid: S.accountUuid(),
        encrypted_name: tag.encrypted_name,
        color: tag.color,
      });
      try {
        await S.verifyRecord(tagPayload, tag.metadata_sig, V.TAG_METADATA_TYPE);
      } catch (cause) {
        throw describe('tag', tag.uuid, cause);
      }
      counts.tags += 1;
    }

    // Both listings, like READ_EVERYTHING: the trash is a separate view, and
    // a trashed row still carries a metadata_sig that must still verify.
    const rows = [
      ...(await A.listEntries(vault.uuid)),
      ...(await A.listEntries(vault.uuid, { trashed: true })),
    ];
    for (const entry of rows) {
      const fields = {};
      for (const row of entry.entry_fields) fields[row.field_id] = row.encrypted_value;
      const entryPayload = V.entryMetadataPayload({
        entry_uuid: entry.uuid,
        vault_uuid: entry.vault,
        signer_account_uuid: S.accountUuid(),
        entry_type: entry.type,
        folder_uuid: entry.folder,
        encrypted_name: entry.encrypted_name,
        encrypted_notes: entry.encrypted_notes,
        key_version: entry.key_version,
        entry_version: entry.entry_version,
        is_favorite: entry.is_favorite,
        tag_uuids: entry.tags,
        fields,
      });
      try {
        await S.verifyRecord(entryPayload, entry.metadata_sig, V.ENTRY_METADATA_TYPE);
      } catch (cause) {
        throw describe('entry', entry.uuid, cause);
      }
      counts.entries += 1;
    }
  }

  return counts;
}
"""


class CorpusBrowserReplayTests(PlaywrightTestCase):
    fixtures = [str(compat.load("v1").rows)]

    def setUp(self):
        super().setUp()
        self.corpus = compat.load("v1")
        self.user = get_user_model().objects.get(
            username=self.corpus.credentials["username"]
        )
        self.login_as(self.user)
        # The unlock screen never calls out to pwnedpasswords itself, but
        # onboarding does, and stubbing it unconditionally is what keeps
        # this test from depending on the network regardless of which path
        # a future change routes it through.
        self.page.route(
            CORPUS_ROUTE,
            lambda route: route.fulfill(
                status=200, body="0000000000000000000000000000000000000:1\n"
            ),
        )

    def _unlock(self):
        """Navigate to the vault and unlock with the corpus credentials.

        Waits for window.vaultSession.isUnlocked() to return true.
        """
        self.page.goto(f"{self.live_server_url}/vault")
        self.page.wait_for_selector("input[autocomplete='current-password']")
        self.page.fill(
            "input[autocomplete='current-password']",
            self.corpus.credentials["vault_master_password"],
        )
        self.page.fill(
            "input[spellcheck='false']", self.corpus.credentials["secret_key"]
        )
        self.page.click("button:has-text('Unlock')")
        self.page.wait_for_function(
            "() => window.vaultSession && window.vaultSession.isUnlocked()",
            timeout=60000,
        )

    def test_a_browser_opens_every_row_of_the_frozen_account(self):
        """The whole manifest, compared in one shot - never a walk of
        selected keys. A partial comparison would pass on a reader that
        silently dropped a vault, a folder, or a field.
        """
        self._unlock()

        # A failed AEAD open inside READ_EVERYTHING reaches here as a bare
        # WebCrypto ``OperationError`` - correct, but illegible to a reader
        # two years from now with no WebCrypto background: nothing in that
        # name says corpus, decryption, or format. TimeoutError is left to
        # propagate as itself - it means Playwright never got an answer, not
        # that the answer it got failed to decrypt, and relabelling it here
        # would send the reader to the wrong cause. Every other Error raised
        # by the evaluate is re-raised as an AssertionError that names what
        # actually happened, chained so the original OperationError and its
        # stack stay visible.
        try:
            read = self.page.evaluate(READ_EVERYTHING)
        except PlaywrightTimeoutError:
            raise
        except PlaywrightError as exc:
            raise AssertionError(
                "The frozen v1 corpus no longer opens with today's vault "
                "bundle - a format or algorithm change has broken "
                f"compatibility with data already written. {exc}"
            ) from exc

        self.assertEqual(read, self.corpus.manifest)

    def test_every_signed_row_still_verifies(self):
        """Decrypting is only half of opening an account: every vault,
        folder, tag and entry also carries a metadata_sig that a real client
        checks before it will show the row to a user (vault_reader.js).
        READ_EVERYTHING never calls that path - it only decrypts - so a
        canonical-encoding regression that broke every signature without
        touching a single ciphertext byte would pass the test above and go
        unnoticed. This is the other half.

        Counted against the manifest before anything is verified, the same
        discipline the other two replays use: a loop that verified nothing
        would otherwise pass silently, which is the exact gap this test
        exists to close.
        """
        manifest_vaults = self.corpus.manifest["vaults"]
        expected = {
            "vaults": len(manifest_vaults),
            "folders": sum(len(v["folders"]) for v in manifest_vaults),
            "tags": sum(len(v["tags"]) for v in manifest_vaults),
            "entries": sum(len(v["entries"]) for v in manifest_vaults),
        }
        for kind, count in expected.items():
            self.assertGreater(count, 0, f"expected at least one {kind}")

        self._unlock()

        # Same treatment as the manifest read above: a signature failure
        # reaches here as a bare WebCrypto rejection or the message this
        # script's own `describe()` builds, both illegible to a reader with
        # no context. Re-raised as an AssertionError that names what this
        # test actually checks, chained so the original error stays visible.
        try:
            counts = self.page.evaluate(VERIFY_EVERY_SIGNATURE)
        except PlaywrightTimeoutError:
            raise
        except PlaywrightError as exc:
            raise AssertionError(
                "The frozen v1 corpus's signatures no longer verify against "
                "today's vault bundle - a canonical encoding or signing "
                "change has broken compatibility with data already "
                f"signed. {exc}"
            ) from exc

        self.assertEqual(counts, expected)
