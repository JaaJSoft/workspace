// Entry point for the vendored vault crypto bundle.
//
// It publishes one global rather than exporting ES modules: the module's Alpine
// components stay classic scripts, which is what keeps them loadable by the
// node:vm test loader.
import { toBase64Url, fromBase64Url, randomBytes, equalBytes } from './src/vault/encoding.js';
import {
  AD, RESERVED_FIELD_IDS, ENTRY_COLUMN_FIELD_IDS, VAULT_FIELD_IDS, FOLDER_FIELD_IDS,
  TAG_FIELD_IDS, qualifyFieldId, associatedData,
} from './src/vault/ad.js';
import {
  currentSuite, isCurrent, mayResign, installTestManifest, UnsupportedAlgorithmError, suiteEntry,
  implementedIds,
} from './src/vault/suites.js';
import {
  KDF_DIRECT, KDF_HKDF_SHA256,
  encodeCiphertext, decodeCiphertext,
  encodePublicKey, decodePublicKey,
} from './src/vault/wire.js';
import {
  assertAccountKdf, deriveAmk, hkdf, assertArchiveParams, deriveArchiveKey,
} from './src/vault/kdf.js';
import { seal, open, importAeadKey, registerAead } from './src/vault/aead.js';
import { hpkeSeal, hpkeOpen, hpkeRecipient } from './src/vault/hpke.js';
import { canonicalCbor, cborSizeBound, decodeCbor, encodeCbor } from './src/vault/cbor.js';
import { sign, verify, signBytes, verifyBytes, importSigner } from './src/vault/sign.js';
import { crockfordEncode, crockfordDecode } from './src/vault/crockford.js';
import {
  base32Decode, base32Encode, parseOtpauth, normalizeTotpInput, importTotpKey, totpCode,
  totpSecondsRemaining,
} from './src/vault/totp.js';
import {
  VAULT_METADATA_TYPE, vaultMetadataPayload, ENTRY_METADATA_TYPE, FOLDER_METADATA_TYPE,
  TAG_METADATA_TYPE, entryMetadataPayload, folderMetadataPayload, tagMetadataPayload,
} from './src/vault/metadata.js';
import { uuidV7 } from './src/vault/uuid.js';

window.vaultCrypto = {
  toBase64Url,
  fromBase64Url,
  randomBytes,
  equalBytes,
  AD,
  RESERVED_FIELD_IDS,
  ENTRY_COLUMN_FIELD_IDS,
  VAULT_FIELD_IDS,
  FOLDER_FIELD_IDS,
  TAG_FIELD_IDS,
  qualifyFieldId,
  associatedData,
  // A getter on the root literal, not a spread: every read sees the suite the
  // test build may have switched before first use.
  get CURRENT_SUITE() { return currentSuite(); },
  isCurrent,
  mayResign,
  UnsupportedAlgorithmError,
  suiteEntry,
  implementedIds,
  KDF_DIRECT,
  KDF_HKDF_SHA256,
  encodeCiphertext,
  decodeCiphertext,
  encodePublicKey,
  decodePublicKey,
  assertAccountKdf,
  deriveAmk,
  hkdf,
  assertArchiveParams,
  deriveArchiveKey,
  seal,
  open,
  importAeadKey,
  registerAead,
  hpkeSeal,
  hpkeOpen,
  hpkeRecipient,
  canonicalCbor,
  cborSizeBound,
  decodeCbor,
  encodeCbor,
  sign,
  verify,
  signBytes,
  verifyBytes,
  importSigner,
  crockfordEncode,
  crockfordDecode,
  base32Decode,
  base32Encode,
  parseOtpauth,
  normalizeTotpInput,
  importTotpKey,
  totpCode,
  totpSecondsRemaining,
  VAULT_METADATA_TYPE,
  vaultMetadataPayload,
  ENTRY_METADATA_TYPE,
  FOLDER_METADATA_TYPE,
  TAG_METADATA_TYPE,
  entryMetadataPayload,
  folderMetadataPayload,
  tagMetadataPayload,
  uuidV7,
};

// eslint-disable-next-line no-undef
if (VAULT_TEST_BUILD) window.vaultCrypto.installTestManifest = installTestManifest;
