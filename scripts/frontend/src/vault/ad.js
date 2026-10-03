import { declaredEntry, storedHpkeFormat } from './suites.js';

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

// A slot's name, not its bytes: which prefix it gets, and whether the wire
// header goes in front, depends on the format of the ciphertext it opens -
// and only the AEAD layer, holding that header, can decide it.
class AdContext {
  constructor(body) {
    ascii(body);
    this.body = body;
    Object.freeze(this);
  }
}

// A context is spelled for the header's format; raw bytes are taken as the
// body verbatim, with the header still in front under a format that wants it.
// Anything else is refused: a string would be rejected by WebCrypto under
// format 1 but coerced to garbage bytes by Uint8Array.set under format 2.
// ArrayBuffer.isView rather than instanceof, which fails across realms.
export function associatedData(context, header) {
  const format = declaredEntry('format', header[0]);
  let body;
  if (context instanceof AdContext) {
    body = ascii(format.ad_prefix + context.body);
  } else if (ArrayBuffer.isView(context) && context.BYTES_PER_ELEMENT === 1) {
    body = context;
  } else {
    throw new TypeError('associated data must be a context or bytes');
  }
  if (!format.header_in_ad) return body;
  const out = new Uint8Array(header.length + body.length);
  out.set(header, 0);
  out.set(body, header.length);
  return out;
}

export const RESERVED_FIELD_IDS = Object.freeze(['username', 'password', 'totp', 'uri']);

// Carried by VaultEntry.encrypted_name / encrypted_notes, which live in another
// table and so escape unique(entry, field_id). An EntryField deriving the same
// associated data would let a ciphertext be swapped between the two and still
// verify, so it may never produce them.
export const ENTRY_COLUMN_FIELD_IDS = Object.freeze(['name', 'notes']);

// Closed for the same reason as RESERVED_FIELD_IDS: an open list would let a
// vault field derive an associated data string an entry field can also derive,
// and a ciphertext could be moved between the two and still verify.
export const VAULT_FIELD_IDS = Object.freeze(['name', 'description']);

// Closed at one identifier each, for the same reason as VAULT_FIELD_IDS.
export const FOLDER_FIELD_IDS = Object.freeze(['name']);
export const TAG_FIELD_IDS = Object.freeze(['name']);

const CUSTOM_PREFIX = 'custom:';

// The account identifier in these strings is the UUID of the account's
// AccountIdentity row, not a user id: Django's auth.User has an integer
// primary key, which is enumerable and reassignable after a deletion - an
// associated data string another human could one day inherit.
export const AD = {
  unwrapInfo: () => ascii('v1|unwrap'),
  entryKeyInfo: (entryUuid) => ascii(`v1|entry-key|${uuid(entryUuid)}`),
  kexPrivAd: (accountUuid) => new AdContext(`account-kex-priv|${uuid(accountUuid)}`),
  sigPrivAd: (accountUuid) => new AdContext(`account-sig-priv|${uuid(accountUuid)}`),
  entryFieldAd: (entryUuid, fieldName) =>
    new AdContext(`entry-field|${uuid(entryUuid)}|${fieldName}`),
  kexPubPayload: (accountUuid, kexPubB64) =>
    ascii(`v1|account-kex-pub|${uuid(accountUuid)}|${kexPubB64}`),
  // Format 2 names the suite inside the info: a stored hpke_suite someone
  // edited then fails to open instead of selecting another suite silently.
  vaultKeyInfo: (vaultUuid, recipientUuid, hpkeSuite) => {
    const base = `vault-key|${uuid(vaultUuid)}|${uuid(recipientUuid)}`;
    const format = storedHpkeFormat(hpkeSuite);
    if (format === 1) return ascii(`v1|${base}`);
    const hex = (n) => n.toString(16).padStart(4, '0');
    const descriptor = `${hex(hpkeSuite.kem_id)}-${hex(hpkeSuite.kdf_id)}-${hex(hpkeSuite.aead_id)}`;
    return ascii(`${declaredEntry('format', format).ad_prefix}${base}|${descriptor}`);
  },
  vaultMetaInfo: (vaultUuid) => ascii(`v1|vault-meta|${uuid(vaultUuid)}`),
  vaultFieldAd: (vaultUuid, field) => {
    if (!VAULT_FIELD_IDS.includes(field)) {
      throw new Error(`${field} is not a vault metadata field`);
    }
    return new AdContext(`vault-field|${uuid(vaultUuid)}|${field}`);
  },
  folderFieldAd: (folderUuid, field) => {
    if (!FOLDER_FIELD_IDS.includes(field)) {
      throw new Error(`${field} is not a folder metadata field`);
    }
    return new AdContext(`folder-field|${uuid(folderUuid)}|${field}`);
  },
  tagFieldAd: (tagUuid, field) => {
    if (!TAG_FIELD_IDS.includes(field)) {
      throw new Error(`${field} is not a tag metadata field`);
    }
    return new AdContext(`tag-field|${uuid(tagUuid)}|${field}`);
  },
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
