// Entry point for the vendored vault crypto bundle.
//
// It publishes one global rather than exporting ES modules: the module's Alpine
// components stay classic scripts, which is what keeps them loadable by the
// node:vm test loader.
import { toBase64Url, fromBase64Url, randomBytes, equalBytes } from './src/vault/encoding.js';
import { AD, RESERVED_FIELD_IDS, ENTRY_COLUMN_FIELD_IDS, qualifyFieldId } from './src/vault/ad.js';
import {
  FORMAT_VERSION, AEAD_AES_256_GCM, KDF_DIRECT, KDF_HKDF_SHA256, PUBKEY_ALG_X25519,
  UnsupportedVersionError, encodeCiphertext, decodeCiphertext,
  encodePublicKey, decodePublicKey,
} from './src/vault/wire.js';
import { ARGON2_PARAMS, deriveAmk, hkdf } from './src/vault/kdf.js';
import { seal, open } from './src/vault/aead.js';
import { HPKE_SUITE_V1, hpkeSeal, hpkeOpen } from './src/vault/hpke.js';
import { canonicalCbor, decodeCbor } from './src/vault/cbor.js';
import { SIG_ALG_ED25519, sign, verify, signBytes, verifyBytes } from './src/vault/sign.js';

window.VaultCrypto = {
  toBase64Url,
  fromBase64Url,
  randomBytes,
  equalBytes,
  AD,
  RESERVED_FIELD_IDS,
  ENTRY_COLUMN_FIELD_IDS,
  qualifyFieldId,
  FORMAT_VERSION,
  AEAD_AES_256_GCM,
  KDF_DIRECT,
  KDF_HKDF_SHA256,
  PUBKEY_ALG_X25519,
  UnsupportedVersionError,
  encodeCiphertext,
  decodeCiphertext,
  encodePublicKey,
  decodePublicKey,
  ARGON2_PARAMS,
  deriveAmk,
  hkdf,
  seal,
  open,
  HPKE_SUITE_V1,
  hpkeSeal,
  hpkeOpen,
  canonicalCbor,
  decodeCbor,
  SIG_ALG_ED25519,
  sign,
  verify,
  signBytes,
  verifyBytes,
};
