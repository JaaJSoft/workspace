import { randomBytes } from './encoding.js';
import { CURRENT_SUITE, UnsupportedAlgorithmError, declaredEntry, markImplemented } from './suites.js';
import { HEADER_LENGTH, decodeCiphertext, encodeCiphertext } from './wire.js';
import { associatedData } from './ad.js';

const KEY_LENGTH = 32;
const AEADS = new Map();

const isRawKey = (key) => ArrayBuffer.isView(key) || key instanceof ArrayBuffer;

// byteLength, not length: an ArrayBuffer and a DataView are raw material this
// module accepts, and neither has a `length` at all - measuring by it rejects
// a perfectly good 32-byte key with "got undefined".
function assertRawKeyLength(key) {
  if (key.byteLength !== KEY_LENGTH) {
    throw new Error(`aead needs a ${KEY_LENGTH}-byte key, got ${key.byteLength}`);
  }
}

// WebCrypto picks the AES variant from the key, so a 16-byte one would
// quietly produce AES-128-GCM under a header still declaring AES-256-GCM -
// the agility byte would be a lie. Checked on every seal and open, not only
// at import, so a handle reaching this impl by any other route than its own
// importKey is refused rather than trusted.
function assertGcmHandle(handle) {
  const { name, length } = (handle && handle.algorithm) || {};
  if (name !== 'AES-GCM' || length !== KEY_LENGTH * 8) {
    throw new Error(`aes-256-gcm needs an AES-GCM ${KEY_LENGTH * 8}-bit key`);
  }
}

const aes256Gcm = {
  ivLength: 12,
  async importKey(raw, usages) {
    assertRawKeyLength(raw);
    return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, usages);
  },
  async seal(handle, iv, plaintext, ad) {
    assertGcmHandle(handle);
    return new Uint8Array(await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv, additionalData: ad, tagLength: 128 }, handle, plaintext));
  },
  async open(handle, iv, ciphertext, ad) {
    assertGcmHandle(handle);
    return new Uint8Array(await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv, additionalData: ad, tagLength: 128 }, handle, ciphertext));
  },
};

export function registerAead(id, impl) {
  if (!Number.isInteger(id) || id < 0 || id > 0xff) {
    throw new Error(`aead id must be an integer in [0, 255], got ${JSON.stringify(id)}`);
  }
  const declared = declaredEntry('aead', id);
  if (!declared) throw new Error(`aead ${id} is not declared in the manifest`);
  if (impl.ivLength !== declared.iv_length) {
    throw new Error(
      `aead ${id} declares iv_length ${declared.iv_length} in the manifest, impl offers ${impl.ivLength}`
    );
  }
  if (AEADS.has(id)) throw new Error(`aead ${id} is already registered`);
  AEADS.set(id, impl);
  markImplemented('aead', id);
}

registerAead(0x01, aes256Gcm);

// A token only this module holds, so a Keyring can be produced by
// importAeadKey and by nothing else - `new Keyring(anything)` from outside
// falls through to the else branch below and throws.
const KEYRING_TOKEN = Symbol('vault keyring');

// One non-extractable handle per registered AEAD, imported once from the raw
// bytes the caller then zeroes. The keyring is how a session keeps a key that
// opens format 1 and format 2, AES-GCM and whatever comes next, without ever
// holding raw key bytes. The handles live in a private field, not a public
// property: nothing outside this module can read or replace them.
class Keyring {
  #handles;
  constructor(token, handles) {
    if (token !== KEYRING_TOKEN) throw new Error('Keyring is constructed only by importAeadKey');
    this.#handles = handles;
  }
  handleFor(aeadId) {
    return this.#handles.get(aeadId);
  }
}

// Both usages by default: a vault metadata key seals and opens across one
// session. async so a bad length arrives as a rejection like every other
// failure here.
export async function importAeadKey(raw, usages = ['encrypt', 'decrypt']) {
  assertRawKeyLength(raw);
  const handles = new Map();
  // Snapshots the AEADs registered at this moment: a script registering a
  // further id must run before this call, or that id is simply absent here.
  for (const [id, impl] of AEADS) handles.set(id, await impl.importKey(raw, usages));
  return new Keyring(KEYRING_TOKEN, handles);
}

// A bare CryptoKey is refused: nothing here can tell which AEAD, or which key
// size, it was imported for, so sealing under it could write a header that
// lies about the algorithm.
async function handleFor(key, aeadId, usage) {
  if (key instanceof Keyring) {
    const handle = key.handleFor(aeadId);
    if (!handle) throw new UnsupportedAlgorithmError('aead', aeadId);
    return handle;
  }
  if (!isRawKey(key)) throw new Error('aead key must be raw bytes or a keyring from importAeadKey');
  assertRawKeyLength(key);
  return AEADS.get(aeadId).importKey(key, [usage]);
}

// The iv is drawn by default; pinning it is for the parity vectors, where
// determinism is the point. 96 random bits are safe here only because the key
// is per entry: the seals under one key stay far below the birthday bound.
export async function seal(key, plaintext, context, { iv, keyVersion, kdfId }) {
  const aeadId = CURRENT_SUITE.aeadId;
  const impl = AEADS.get(aeadId);
  const nonce = iv || randomBytes(impl.ivLength);
  // The header comes first because format 2 authenticates it.
  const header = encodeCiphertext({
    aeadId, kdfId, keyVersion, iv: nonce, ciphertext: new Uint8Array(0),
  }).slice(0, HEADER_LENGTH);
  const sealed = await impl.seal(
    await handleFor(key, aeadId, 'encrypt'), nonce, plaintext, associatedData(context, header)
  );
  const out = new Uint8Array(header.length + nonce.length + sealed.length);
  out.set(header, 0);
  out.set(nonce, header.length);
  out.set(sealed, header.length + nonce.length);
  return out;
}

export async function open(key, raw, context) {
  const decoded = decodeCiphertext(raw);
  const impl = AEADS.get(decoded.aeadId);
  // An open failure propagates as-is: never retried with another AD, never
  // returned as partial plaintext. Retrying turns the AD into an oracle.
  return impl.open(
    await handleFor(key, decoded.aeadId, 'decrypt'),
    decoded.iv,
    decoded.ciphertext,
    associatedData(context, decoded.header)
  );
}
