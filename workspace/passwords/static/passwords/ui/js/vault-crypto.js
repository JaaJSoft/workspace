// Zero-knowledge vault cryptography helpers.
// All operations use window.crypto.subtle — no external libraries.
// Format for all AES-GCM ciphertext: base64url(iv[12] || ciphertext || tag[16])

const VaultCrypto = (() => {
  const enc = new TextEncoder();
  const dec = new TextDecoder();

  function b64urlEncode(buf) {
    return btoa(String.fromCharCode(...new Uint8Array(buf)))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function b64urlDecode(str) {
    str = str.replace(/-/g, '+').replace(/_/g, '/');
    while (str.length % 4) str += '=';
    return Uint8Array.from(atob(str), c => c.charCodeAt(0));
  }

  // PBKDF2-SHA256: password is string, salt is Uint8Array, returns CryptoKey
  async function pbkdf2(password, salt, iterations) {
    const baseKey = await crypto.subtle.importKey(
      'raw', enc.encode(password), 'PBKDF2', false, ['deriveBits', 'deriveKey']
    );
    return crypto.subtle.deriveKey(
      { name: 'PBKDF2', hash: 'SHA-256', salt, iterations },
      baseKey,
      { name: 'AES-GCM', length: 256 },
      true,
      ['encrypt', 'decrypt']
    );
  }

  // PBKDF2-SHA256 returning raw 32 bytes (for client_master_hash derivation)
  async function pbkdf2Bits(password, salt, iterations) {
    const baseKey = await crypto.subtle.importKey(
      'raw', enc.encode(password), 'PBKDF2', false, ['deriveBits']
    );
    const bits = await crypto.subtle.deriveBits(
      { name: 'PBKDF2', hash: 'SHA-256', salt, iterations },
      baseKey, 256
    );
    return new Uint8Array(bits);
  }

  // HKDF-SHA256: ikm is CryptoKey (AES-GCM), info string, returns CryptoKey (AES-GCM)
  async function hkdf(ikmKey, info) {
    const ikmRaw = await crypto.subtle.exportKey('raw', ikmKey);
    const ikmImported = await crypto.subtle.importKey(
      'raw', ikmRaw, 'HKDF', false, ['deriveKey']
    );
    return crypto.subtle.deriveKey(
      { name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(32), info: enc.encode(info) },
      ikmImported,
      { name: 'AES-GCM', length: 256 },
      true,
      ['encrypt', 'decrypt']
    );
  }

  // Derive encryption key from master password + kdf_salt (base64url string) + iterations
  async function deriveEncryptionKey(masterPassword, kdfSaltB64, kdfIterations) {
    const salt = b64urlDecode(kdfSaltB64);
    const stretchedKey = await pbkdf2(masterPassword, salt, kdfIterations);
    return hkdf(stretchedKey, 'enc');
  }

  // Derive client_master_hash from master password + kdf_salt + iterations
  // Returns base64url-encoded string
  async function deriveClientMasterHash(masterPassword, kdfSaltB64, kdfIterations) {
    const salt = b64urlDecode(kdfSaltB64);
    const stretchedKey = await pbkdf2(masterPassword, salt, kdfIterations);
    const stretchedRaw = await crypto.subtle.exportKey('raw', stretchedKey);
    const hashBytes = await pbkdf2Bits(
      b64urlEncode(stretchedRaw),
      enc.encode(masterPassword),
      1
    );
    return b64urlEncode(hashBytes);
  }

  // Generate a new random 32-byte vault key, return as Uint8Array
  function generateVaultKey() {
    return crypto.getRandomValues(new Uint8Array(32));
  }

  // AES-256-GCM encrypt: key is CryptoKey, plaintext is string or Uint8Array
  // Returns base64url(iv[12] || ciphertext || tag[16])
  async function aesEncrypt(key, plaintext) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const data = typeof plaintext === 'string' ? enc.encode(plaintext) : plaintext;
    const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, data);
    const out = new Uint8Array(12 + ciphertext.byteLength);
    out.set(iv, 0);
    out.set(new Uint8Array(ciphertext), 12);
    return b64urlEncode(out);
  }

  // AES-256-GCM decrypt: key is CryptoKey, encoded is base64url(iv || ciphertext || tag)
  // Returns decrypted string (or null on failure)
  async function aesDecrypt(key, encoded) {
    try {
      const bytes = b64urlDecode(encoded);
      const iv = bytes.slice(0, 12);
      const data = bytes.slice(12);
      const plaintext = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, data);
      return dec.decode(plaintext);
    } catch {
      return null;
    }
  }

  // Import raw Uint8Array as an AES-GCM CryptoKey
  async function importVaultKey(rawBytes) {
    return crypto.subtle.importKey('raw', rawBytes, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
  }

  // Setup flow: derive keys, generate vault key, encrypt it, compute hash
  // Returns { clientMasterHash, protectedVaultKey, vaultKeyRaw }
  async function setupVault(masterPassword, kdfSaltB64, kdfIterations) {
    const encKey = await deriveEncryptionKey(masterPassword, kdfSaltB64, kdfIterations);
    const clientMasterHash = await deriveClientMasterHash(masterPassword, kdfSaltB64, kdfIterations);
    const vaultKeyRaw = generateVaultKey();
    const protectedVaultKey = await aesEncrypt(encKey, vaultKeyRaw);
    return { clientMasterHash, protectedVaultKey, vaultKeyRaw };
  }

  // Unlock flow: derive encryption key, decrypt protected_vault_key
  // Returns raw Uint8Array vault key (or null on failure)
  async function unlockVault(masterPassword, kdfSaltB64, kdfIterations, protectedVaultKey) {
    const encKey = await deriveEncryptionKey(masterPassword, kdfSaltB64, kdfIterations);
    try {
      const bytes = b64urlDecode(protectedVaultKey);
      const iv = bytes.slice(0, 12);
      const data = bytes.slice(12);
      const raw = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, encKey, data);
      return new Uint8Array(raw);
    } catch {
      return null;
    }
  }

  return {
    b64urlEncode,
    b64urlDecode,
    deriveEncryptionKey,
    deriveClientMasterHash,
    generateVaultKey,
    aesEncrypt,
    aesDecrypt,
    importVaultKey,
    setupVault,
    unlockVault,
  };
})();
