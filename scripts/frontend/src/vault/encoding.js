// base64url without padding is the transport form of every ciphertext the
// server stores. Browsers only expose base64 with padding, so the
// translation happens here once rather than at each call site.
export function toBase64Url(bytes) {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function fromBase64Url(text) {
  const padded = text.replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export function randomBytes(length) {
  // The CSPRNG is absent outside a secure context, where `crypto.subtle` does
  // not exist either. Left unchecked the failure surfaces as an opaque
  // TypeError from somewhere deep in a key derivation; named here, it says
  // what is actually wrong with the deployment.
  if (typeof crypto === 'undefined' || typeof crypto.getRandomValues !== 'function') {
    throw new Error('no CSPRNG available: the vault requires a secure context');
  }
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  // Belt and braces: getRandomValues fills in place, so this cannot fire here.
  // It holds the same invariant the reference implementation checks for real,
  // its CSPRNG returning a fresh buffer whose length could differ.
  if (bytes.length !== length) throw new Error('CSPRNG returned a short read');
  return bytes;
}

// Constant time: the loop runs over the whole input whatever the outcome, so
// the duration leaks nothing about where two tags first differ.
export function equalBytes(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a[i] ^ b[i];
  return diff === 0;
}
