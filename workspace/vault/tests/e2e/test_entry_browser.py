"""An entry written by a real browser, read back and re-verified.

The parity vectors prove the bundle against the Python reference. They cannot
prove the bundle against the *server*: the server and the reference both encode
with ``cbor2``, so their agreement is circular. ``entry-metadata`` is also the
first payload in the format to carry arrays - the exact structure that once
sent ``cbor-x`` down its iterator branch and made it emit indefinite lengths -
and the server's recursion into those arrays is new. Only a walk observes the
two ends agreeing on those bytes.

The second half is what says covering the tag set and the field set with the
signature bought something: a row deleted straight from the database, which is
what a hostile server does, has to read as tampering in the browser.
"""

from django.core.cache import cache

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.vault.models import EntryField, VaultEntry

GOOD_PASSWORD = "correct-horse-battery-staple-42"
CORPUS_ROUTE = "https://api.pwnedpasswords.com/range/*"

# Seal three fields and a name under the entry key, sign the whole record and
# post it - the same sequence the vault browser will run.
WRITE_ENTRY = """
async (withTag) => {
  const V = window.vaultCrypto, A = window.vaultApi, S = window.vaultSession;
  const enc = new TextEncoder();
  try {
    const vault = (await A.listVaults())[0];
    const entryUuid = V.uuidV7();
    const entryKey = await S.openEntryKey(vault.uuid, vault.wrapped_key, entryUuid);
    const seal = async (field, text) => V.toBase64Url(
      await V.seal(entryKey, enc.encode(text), V.AD.entryFieldAd(entryUuid, field), {
        keyVersion: 1,
        kdfId: V.KDF_HKDF_SHA256,
      })
    );

    let tagUuids = [];
    if (withTag) {
      const tagUuid = V.uuidV7();
      const metaKey = await S.openVaultKey(vault.uuid, vault.wrapped_key);
      const tagName = V.toBase64Url(
        await V.seal(metaKey, enc.encode('Work'), V.AD.tagFieldAd(tagUuid, 'name'), {
          keyVersion: 1,
          kdfId: V.KDF_HKDF_SHA256,
        })
      );
      const tagPayload = V.tagMetadataPayload({
        tag_uuid: tagUuid,
        vault_uuid: vault.uuid,
        signer_account_uuid: S.accountUuid(),
        encrypted_name: tagName,
        color: 'primary',
      });
      await A.createTag({
        uuid: tagUuid,
        vault: vault.uuid,
        encrypted_name: tagName,
        color: 'primary',
        metadata_sig: await S.sign(tagPayload),
      });
      tagUuids = [tagUuid];
    }

    const fields = {
      username: await seal('username', 'octocat'),
      password: await seal('password', 'hunter2'),
      'custom:pin': await seal('custom:pin', '1234'),
    };
    const encryptedName = await seal('name', 'GitHub');
    const payload = V.entryMetadataPayload({
      entry_uuid: entryUuid,
      vault_uuid: vault.uuid,
      signer_account_uuid: S.accountUuid(),
      entry_type: 'login',
      folder_uuid: null,
      encrypted_name: encryptedName,
      encrypted_notes: '',
      key_version: 1,
      entry_version: 1,
      is_favorite: false,
      tag_uuids: tagUuids,
      fields,
    });
    const created = await A.createEntry({
      uuid: entryUuid,
      vault: vault.uuid,
      type: 'login',
      folder: null,
      tags: tagUuids,
      is_favorite: false,
      encrypted_name: encryptedName,
      encrypted_notes: '',
      fields,
      metadata_sig: await S.sign(payload),
    });
    return { status: 201, uuid: created.uuid, vault: vault.uuid };
  } catch (error) {
    return { status: error.status || 0, reason: String(error && error.message) };
  }
}
"""

# Read the entry back, rebuild the payload from what the server returned, and
# check the stored signature over it - then open the name.
VERIFY_ENTRY = """
async (entryUuid) => {
  const V = window.vaultCrypto, A = window.vaultApi, S = window.vaultSession;
  try {
    const vault = (await A.listVaults())[0];
    const entry = await A.getEntry(entryUuid);
    const fields = {};
    for (const row of entry.entry_fields) fields[row.field_id] = row.encrypted_value;
    const payload = V.entryMetadataPayload({
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
    await S.verifyRecord(payload, entry.metadata_sig, V.ENTRY_METADATA_TYPE);

    const entryKey = await S.openEntryKey(vault.uuid, vault.wrapped_key, entry.uuid);
    const name = new TextDecoder().decode(
      await V.open(
        entryKey,
        V.fromBase64Url(entry.encrypted_name),
        V.AD.entryFieldAd(entry.uuid, 'name')
      )
    );
    return { verified: true, name, fields: Object.keys(fields).sort() };
  } catch (error) {
    return { verified: false, reason: String(error && error.message) };
  }
}
"""


class EntryRoundTripTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="owner", email="owner@example.com")
        self.login_as(self.user)
        self.page.route(
            CORPUS_ROUTE,
            lambda route: route.fulfill(
                status=200, body="0000000000000000000000000000000000000:1\n"
            ),
        )

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _onboard(self):
        self.page.goto(f"{self.live_server_url}/vault")
        self.page.wait_for_url("**/vault/onboarding")
        self.page.click("button:has-text('I understand')")
        self.page.fill("input[autocomplete='new-password'] >> nth=0", GOOD_PASSWORD)
        self.page.fill("input[autocomplete='new-password'] >> nth=1", GOOD_PASSWORD)
        self.page.wait_for_selector(
            "button:has-text('Set my master password'):not([disabled])", timeout=15000
        )
        self.page.click("button:has-text('Set my master password')")
        self.page.wait_for_selector("#recovery-key-acknowledged", timeout=60000)
        self.secret = self.page.inner_text("[data-recovery-key]")
        self.page.check("#recovery-key-acknowledged")
        self.page.click("button:has-text('Create my first vault')")
        self.page.wait_for_url("**/vault")
        return self.secret

    def _unlock(self):
        self.page.wait_for_selector("input[autocomplete='current-password']")
        self.page.fill("input[autocomplete='current-password']", GOOD_PASSWORD)
        self.page.fill("input[spellcheck='false']", self.secret)
        self.page.click("button:has-text('Unlock')")
        self.page.wait_for_function(
            "() => window.vaultSession && window.vaultSession.isUnlocked()",
            timeout=60000,
        )

    def _write(self, with_tag=False):
        self._onboard()
        self._unlock()
        written = self.page.evaluate(WRITE_ENTRY, with_tag)
        self.assertEqual(written["status"], 201, written.get("reason"))
        return written

    def _reopen_and_verify(self, entry_uuid):
        self.page.reload()
        self._unlock()
        return self.page.evaluate(VERIFY_ENTRY, entry_uuid)

    def test_an_entry_written_by_the_browser_is_read_back_and_verifies(self):
        """The one assertion the vectors cannot make: the server accepted a
        signature the browser produced, over a payload carrying arrays."""
        written = self._write()
        read = self._reopen_and_verify(written["uuid"])
        self.assertTrue(read["verified"], read.get("reason"))
        self.assertEqual(read["fields"], ["custom:pin", "password", "username"])
        self.assertEqual(read["name"], "GitHub")

    def test_no_field_value_reaches_the_database_in_the_clear(self):
        written = self._write()
        stored = EntryField.objects.filter(entry_id=written["uuid"])
        self.assertEqual(stored.count(), 3)
        for field in stored:
            self.assertNotIn("hunter2", field.encrypted_value)
            self.assertNotIn("octocat", field.encrypted_value)

    def test_a_field_removed_behind_the_client_reads_as_tampering(self):
        written = self._write()
        deleted, _ = EntryField.objects.filter(
            entry_id=written["uuid"], field_id="custom:pin"
        ).delete()
        self.assertEqual(deleted, 1)

        read = self._reopen_and_verify(written["uuid"])
        self.assertFalse(read["verified"])
        # The reason matters: a crash on the missing row would also leave
        # verified false, and would prove nothing about the signature.
        self.assertIn("signature", read["reason"])

    def test_a_tag_detached_behind_the_client_reads_as_tampering(self):
        written = self._write(with_tag=True)
        entry = VaultEntry.objects.get(uuid=written["uuid"])
        self.assertEqual(entry.tags.count(), 1)
        entry.tags.clear()

        read = self._reopen_and_verify(written["uuid"])
        self.assertFalse(read["verified"])
        self.assertIn("signature", read["reason"])
