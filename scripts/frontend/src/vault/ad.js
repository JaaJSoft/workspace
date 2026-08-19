// The info and associated-data catalogue. These strings ARE the format:
// changing one breaks the decryption of everything already written with it,
// and nothing fails until a user opens the entry. ASCII only, `|` separator,
// lowercase RFC 4122 UUIDs, no trailing newline.
const ascii = (text) => {
  // The reference encodes these with a strict ASCII codec and raises on
  // anything else. Field identifiers come from the user, so without this a
  // custom field named `café` would build valid associated data in the browser
  // and crash the implementation it is supposed to match.
  if (/[^\x00-\x7f]/.test(text)) {
    throw new Error(`associated data must be ASCII: ${text}`);
  }
  return new TextEncoder().encode(text);
};
const uuid = (value) => String(value).toLowerCase();

export const RESERVED_FIELD_IDS = Object.freeze(['username', 'password', 'totp', 'uri']);

// Carried by VaultEntry.encrypted_name / encrypted_notes, which live in another
// table and so escape unique(entry, field_id). An EntryField deriving the same
// associated data would let a ciphertext be swapped between the two and still
// verify, so it may never produce them.
export const ENTRY_COLUMN_FIELD_IDS = Object.freeze(['name', 'notes']);

export const AD = {
  unwrapInfo: () => ascii('v1|unwrap'),
  entryKeyInfo: (entryUuid) => ascii(`v1|entry-key|${uuid(entryUuid)}`),
  kexPrivAd: (userUuid) => ascii(`v1|account-kex-priv|${uuid(userUuid)}`),
  sigPrivAd: (userUuid) => ascii(`v1|account-sig-priv|${uuid(userUuid)}`),
  entryFieldAd: (entryUuid, fieldName) => ascii(`v1|entry-field|${uuid(entryUuid)}|${fieldName}`),
  kexPubPayload: (userUuid, kexPubB64) => ascii(`v1|account-kex-pub|${uuid(userUuid)}|${kexPubB64}`),
  vaultKeyInfo: (vaultUuid, recipientUuid) =>
    ascii(`v1|vault-key|${uuid(vaultUuid)}|${uuid(recipientUuid)}`),
};

export function qualifyFieldId(fieldId) {
  if (fieldId.startsWith('custom:')) return fieldId;
  if (RESERVED_FIELD_IDS.includes(fieldId)) return fieldId;
  return `custom:${fieldId}`;
}
