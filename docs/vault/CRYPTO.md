# Vault cryptography

How the end-to-end encrypted vault protects its data, and why the choices that
look arbitrary from the outside are what they are. Written for whoever reviews
or extends the cryptographic code; the module's own documentation lands with
its interface.

## Key hierarchy

1. The vault password and the account `secret_key` derive an **account master
   key** through Argon2id.
2. HKDF-SHA256 derives an **unwrap key** from the AMK, which is the only thing
   that opens the account's sealed private keys.
3. Each account owns an X25519 key pair (key exchange) and an Ed25519 key pair
   (signatures); both private keys are sealed under the unwrap key.
4. Each vault owns a symmetric **vault key**, sealed to a member's X25519 public
   key with HPKE. Opening a vault means unsealing that key.
5. Each entry derives its own key from the vault key with HKDF, and every
   encrypted field is bound to its entry by associated data.

The server stores ciphertext and public keys only. It cannot derive any of the
above, and none of the steps runs anywhere but the browser.

## Why `secret_key` is Argon2's `K`, not part of the password

`secret_key` is passed as the Argon2 `secret` parameter (RFC 9106 §3.1), never
concatenated with the password. Concatenation would make the two values
interchangeable to the KDF, so an attacker who learns one gains the other's
search space for free; as `K` the secret is a separate input the attacker has to
own outright before password guessing becomes worth anything.

This has a practical consequence in both implementations. Python calls
`argon2.low_level.core()` rather than `hash_secret_raw()`, because only the
low-level entry point exposes `secret`; the browser uses `hash-wasm` for the
same reason, and `argon2-browser` is unusable here since it never surfaces the
parameter.

## The parity vectors

`workspace/vault/tests/crypto_vectors.json` is the contract between the browser
bundle and the Python reference implementation. Every value in it was produced
by the reference from fixed inputs, and the browser test suite replays each one.
An encoding divergence between the two implementations is the most likely way
this module breaks, and its natural symptom is a user who can no longer open a
vault; the vectors turn that into a failing CI job instead.

Regenerate them after any deliberate change to a primitive or to the associated
data catalogue:

```bash
uv run python -m workspace.vault.tests.reference.generate_vectors
```

A regeneration that changes a byte is a breaking change, not a formatting
detail. `test_the_committed_file_matches_a_fresh_generation` fails whenever the
committed file and a fresh run disagree.

Not every vector can be replayed by comparing bytes. Three cannot, for reasons
that belong to the primitives rather than to this code, and each is worth
knowing before writing another implementation:

- **HPKE** draws a fresh ephemeral key on every seal and `@hpke/core` offers no
  way to pin it, so two seals of the same plaintext never match. The browser
  suite asserts interoperability instead - it opens what the reference sealed,
  its own seals reopen, and a different `info` fails.
- **Ed25519 signatures are not deterministic everywhere.** Safari signs with
  added noise rather than following RFC 8032's deterministic construction, so
  its signature bytes legitimately differ from the reference for the same key
  and message, while still verifying. Signatures are therefore verified rather
  than compared on the browser side; the Node suite keeps byte equality, its
  engine being deterministic. Nothing is weaker for it - a hedged signature
  protects against a poor source of randomness - but a test that compares
  signature bytes across engines will fail for no good reason.
- **Canonical CBOR** forbids floats, but `cbor-x` encodes any integer outside
  the 32-bit range as a float64, which is exactly what a millisecond timestamp
  is. Those values are converted to `BigInt` before encoding so the integer
  encoding of the reference comes out. Two further inputs have no encoding both
  sides agree on and are refused rather than guessed: negative integers between
  -2^31-1 and -2^32, and map keys that become one key once NFC-normalised.

## The export archive

`vault_archive.js` writes an archive and `tests/reference/archive.py` reads one.
Neither is the other's source: both follow this section, and a change to the
format changes all three together.

An archive is a 50-byte public header followed by a single sealed payload.

| Offset | Length | Field | Value |
|---:|---:|---|---|
| 0 | 7 | magic | `VLTARCH` |
| 7 | 1 | container version | `0x01` |
| 8 | 1 | KDF id | `0x01`, Argon2id |
| 9 | 4 | `m` | big-endian uint32, in KiB |
| 13 | 4 | `t` | big-endian uint32 |
| 17 | 1 | `p` | |
| 18 | 32 | salt | drawn for each export |
| 50 | - | payload | the wire format: a 6-byte header (format version, AEAD id, KDF id, two-byte key version, IV length), the 12-byte IV, then the AES-256-GCM ciphertext |

- **The key.** Argon2id over the NFC-normalised passphrase, with the header's
  salt and parameters and no `secret` - an export has no `secret_key` - then
  HKDF-SHA256 with info `v1|archive-key`. The payload declares that last step:
  KDF id `0x01` (HKDF-SHA256) and key version `0`.
- **The associated data** is the whole 50-byte header, so an edited header
  byte fails as tampering rather than as a wrong passphrase. The payload's own
  header is not covered by it, so a reader treats that KDF id as untrusted.
- **The parameter bounds** are checked before anything is derived: `m` in
  [8192, 1048576], `t` in [1, 10], `p` in [1, 4]. A file declaring 4 GiB is
  refused rather than tried. The bounds may widen and must never narrow:
  narrowing rejects archives already written.
- **One seal per key.** Each export draws a fresh salt, hence a fresh key, so
  the 96-bit IV is used once. An incremental archive that sealed twice under
  one key would leave that bound.
- **The plaintext** is CBOR, and not the canonical form the signed payloads
  use: nothing ever compares an archive's bytes, so strings are stored exactly
  as given, without NFC, and map keys keep their insertion order. Vaults,
  folders, tags and entries carry file-local ids rather than account UUIDs, and
  trashed entries are kept and marked. It is encoded into a buffer the writer
  allocates and wipes, sized so the encoder never grows a buffer of its own.

## The reference implementation

`workspace/vault/tests/reference/` is a second, independent implementation of
the key hierarchy above, in Python. It exists to generate the vectors and to be
compared against, and **application code must never import it**: a server-side
path able to perform these operations would be a server-side path able to
decrypt, which is the property this module is built to deny. It lives under
`tests/` so `coverage` omits it and the module's coverage floor stays honest.

## Bundle budget

`vault-crypto.js` is on the unlock path, so it carries a budget of 75 KB
gzipped, enforced by `workspace/vault/tests/js/vault_bundle.test.js`. Argon2id's
WebAssembly is inlined into it deliberately: one artifact to verify in CI, and
one fewer request at the most sensitive moment of the flow.

The PDF generator and the password strength estimator would each break that
budget on their own. They serve onboarding and password rotation only, never
unlocking and never the entry pages, so they ship as a second bundle,
`vault-onboarding.js`, for the screens that need them to load on demand. The
integrity test asserts they have not leaked back into the main one.

Both artifacts are built by the shared frontend project and committed to
statics:

```bash
cd scripts/frontend && npm run build:vault && npm run build:vault-onboarding
```
