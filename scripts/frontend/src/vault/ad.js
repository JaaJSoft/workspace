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

const CUSTOM_PREFIX = 'custom:';

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

// The AD component of a STORED field id - identity, never a transformation:
// `x` and `custom:x` are both legal rows under unique(entry, field_id), so a
// mapping that collapsed them onto one AD would let their ciphertexts be
// swapped and still verify. Producing a stored id from a label is the write
// path's job.
export function qualifyFieldId(fieldId) {
  if (RESERVED_FIELD_IDS.includes(fieldId)) return fieldId;
  if (!fieldId.startsWith(CUSTOM_PREFIX)) {
    throw new Error(`field id ${fieldId} is neither reserved nor ${CUSTOM_PREFIX}-prefixed`);
  }
  const label = fieldId.slice(CUSTOM_PREFIX.length);
  if (!label || label.includes(':')) {
    throw new Error(`field id ${fieldId} carries a malformed custom label`);
  }
  return fieldId;
}
