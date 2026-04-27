# Passwords — Cryptographic Architecture (Design Proposal)

> **Status**: Design proposal. Not yet implemented. Targets a rewrite of the crypto flow of `workspace.passwords` (PR #103 and successors). The current implementation deviates on several points (see `PR-103-REVIEW.md`). This document describes **what the vault should become**, independent of current bugs.

> **Audience**: anyone implementing, reviewing, or auditing the passwords module. Assumes familiarity with symmetric / asymmetric crypto, KDFs, AEADs, and the Django/Alpine stack used elsewhere in the project.

> **Reading convention**: most cross-cutting decisions have been resolved inline (look for explicit *"chosen"* / *"rejected"* phrasing). Remaining inline **TODO** blockquotes mark items genuinely deferred to a later milestone (typically: PQ standards landing, post-launch telemetry-driven tuning). `grep -n "TODO"` over this file lists what is still open.

---

## 1. Purpose

Define a cryptographic architecture for the passwords module that:

1. Holds up against a **passive server compromise** (DB dump, log read, memory snapshot): no secret leaks.
2. Holds up against an **active server compromise** (MITM, modified JS, tampered ciphertext): tampering is detected, sharing does not silently leak the vault key.
3. Makes offline brute-force of exfiltrated data infeasible even when the user chooses a weak password.
4. Is **evolutive**: every algorithm choice can be changed without a flag-day migration.
5. Is **rotation-friendly**: password, vault key, and member access can each rotate independently.
6. Is ready for **post-quantum** primitives when they stabilize, with zero re-architecture.

This is *not* a user guide or an API reference. It is the security blueprint implementations must conform to.

---

## 2. Threat model

### In scope

| Adversary | Capability | Must defend against? |
|-----------|------------|-----------------------|
| Passive server | Reads DB, logs, memory dumps, backups | **Yes** |
| Active server | Can MITM, serve modified JS, swap ciphertexts | **Yes** |
| Network attacker | Passive or active on the wire, with HTTPS downgrade attempts | Yes (TLS + pinning on mobile) |
| Offline brute-force on DB dump | GPU farm, ASIC | Yes (KDF work factor + secret key) |
| Replay of login proofs | Re-submitting a captured login credential | Out of scope — handled by the login layer (Django + SSO IdP). The vault itself has no auth tokens. |
| Peer user misbehaving | Legitimate member abusing their role | Yes (role checks + audit log) |
| Future quantum adversary | Harvests today, decrypts in ≥ 10 years | Yes — reserved PQ slots |

### Out of scope

- Kernel-level malware on the unlocked device (unavoidable).
- Coerced user ("rubber-hose cryptanalysis").
- Side-channels in the browser's WebCrypto implementation (trust boundary).
- Legal compulsion of the server operator (threat-model-dependent; this design limits what can be compelled by keeping keys off-server).

### Explicit non-promises

- **Perfect forward secrecy across sessions for stored data** — by definition, a vault stores persistent secrets the user can retrieve later.
- **Post-compromise security against a *kept* malicious device** — once the raw vault key has been in RAM on a compromised machine, revocation only protects future writes.
- **Silent content protection after a viewer has seen data** — memory is memory.
- **Vault inaccessibility when an OS keystore is bypassed on a biometric-unlocked device.** A device that has been biometric-unlocked at least once stores a sealed copy of `unwrap_key` (§5.1). An attacker with physical access who bypasses the OS keystore (Touch ID / Windows Hello / Android Keystore) recovers `unwrap_key` without needing the vault password. Mitigations are OS-level (keystore strength, secure enclaves), not crypto-level. Users who want the strongest model disable biometric unlock and retype on every launch.

---

## 3. Goals & non-goals

### Goals

- **Zero-knowledge of vault contents**: the server never holds plaintext entry fields, plaintext master passwords, or plaintext symmetric keys.
- **Ciphertext tamper-evidence**: every ciphertext is bound to its context by authenticated data; substitution attacks fail loudly.
- **Ciphertext versioning**: format upgrades without data migration big bang.
- **One account identity, many vaults**: a user has **one** `vault_password` and **one** asymmetric identity. Vaults are independent symmetric key containers wrapped for that identity.
- **Lazy migration**: algorithm or parameter upgrades land on next write to each record; no "stop the world".
- **Independent rotation**: vault password, vault key, and member set rotate independently.

### Non-goals

- **Server-side vault decryption** for features like full-text search. Not feasible without breaking zero-knowledge. Search happens on decrypted data, client-side, and only for entries the client has already unlocked.
- **Account recovery without the master password or secret key**. By design. Users get an emergency kit; losing both means data loss — the same trade-off Bitwarden/1Password make.
- **Fine-grained per-entry sharing in v1**. Per-vault sharing is the unit. Per-entry ACLs are a follow-up.

---

## 4. High-level architecture

```
                        ┌───────────────────────────────────────────────┐
                        │                 SERVER (Django)                │
                        │                                                │
                        │  • Stores opaque ciphertexts + public keys     │
                        │  • Never holds symmetric keys or password      │
                        │  • Enforces authz (Django session) + ratelimit │
                        │  • Holds signed metadata + audit log           │
                        └───────────────────────────────────────────────┘
                                        ▲        ▲
                                        │        │
                          unlock/fetch  │        │  invite/share/revoke
                                        │        │
                        ┌───────────────────────────────────────────────┐
                        │                  CLIENT (browser)              │
                        │                                                │
                        │  • Derives AMK (Argon2id) from password+secret │
                        │  • Holds account private keys (unwrapped)      │
                        │  • Holds vault symmetric keys (unwrapped)      │
                        │  • Encrypts/decrypts entry fields              │
                        │  • Signs metadata                              │
                        │  • Auto-locks on idle                          │
                        └───────────────────────────────────────────────┘
```

The server is a dumb ciphertext-and-metadata store plus an auth endpoint. Every crypto operation happens in the browser.

---

## 5. Key hierarchy

### 5.1 Password model — dedicated vault password, not reused from Django auth

Everywhere in this document, `vault_password` means a **dedicated vault master password**, set by the user at vault onboarding, **never equal** (as a data flow) to the password they type into the Django login form. The two passwords may happen to be the same string if the user chooses so — nothing stops them — but the two authentication paths are architecturally separate. (The name is intentional: the concept is "unlock my vault", not "sign me into the application" — even though there is exactly one `vault_password` per user, shared across all their vaults.)

This is a permanent architectural choice, not a pragmatic v1 compromise. The section below documents what was considered and why the alternatives are off the table.

#### The options considered

| Option         | Summary                                                                                                                    | Zero-knowledge      | Verdict                                                                     |
|----------------|----------------------------------------------------------------------------------------------------------------------------|---------------------|-----------------------------------------------------------------------------|
| **A** (chosen) | Dedicated vault password, handled 100% client-side. Orthogonal to the login identity, whatever that identity mechanism is. | ✅ Intact.           | **Used by this design — permanent.**                                        |
| **B**          | Unify the vault password with the Django login password via an aPAKE (Bitwarden-style, OPAQUE). One password for both.     | ✅ Intact in theory. | **Ruled out** — incompatible with the project's auth direction (see below). |
| **C**          | Reuse the Django login password naively: the server receives it in plaintext at login and derives keys from it.            | ❌ Broken.           | **Never** — security footgun.                                               |

#### Why Option C is a non-starter

Django's authentication backend receives the plaintext password in `request.POST['password']` and passes it to `check_password`. For that brief window, the plaintext exists server-side: on the request, in the view's local scope, and potentially in request-logging middleware, WSGI access logs, APM body capture, Sentry breadcrumbs, reverse-proxy logs, and `nginx`'s `access.log` if `log_format` captures request bodies.

Any single one of those capture paths is a persistent, replayable leak of the master password. "Just don't log bodies" is not defense — it's a convention that silently breaks on the next middleware a team adds. Zero-knowledge must be **structural**, not conventional. Option C is an architectural no-go, not tech debt.

#### Why Option B is permanently ruled out

The project's authentication roadmap includes **OpenID Connect / OAuth 2.0 SSO** as a first-class identity source. Under SSO:

- The user authenticates against an external identity provider (Google Workspace, Microsoft Entra, Keycloak, an enterprise IdP…). The IdP verifies credentials; Django receives only an assertion token (JWT, SAML response, code exchange).
- **Django never sees the user's identity-provider password.** There is no password to unify with, even if we wanted to.
- Some users may log in via password (the existing local accounts), some via SSO, some via both over time. A unified "vault + login" password would only work for the first category and would break for everyone else.

Option B therefore cannot even be implemented coherently across the user base. The only design that treats local-auth users and SSO users the same is Option A: a dedicated vault password independent of the login identity.

This is not a "deferred until later" decision — it's a permanent architectural alignment.

#### Why Option A is the right shape given that direction

- **Auth-method agnostic.** A local-password user, an SSO user, and an API-token user all arrive at the vault with a Django session cookie. Whatever authenticated them doesn't matter: the vault just needs "who is the Django user" and "what is the vault password". The two concerns are cleanly separable.
- **Scope containment.** All changes live in `workspace.passwords`. No touching of `django.contrib.auth`, Knox, the admin, the password-reset flow, the future SSO integration, or existing user records.
- **Blast-radius control.** An admin running `manage.py changepassword alice` or an SSO provider rotating Alice's email only affects her login identity. Her vault is untouched. Under Option B, any login-side change would cascade into vault inaccessibility.
- **The `secret_key` carries the security budget.** A user who reuses a weak password for the vault isn't meaningfully weaker here than they would be under Option B, because the 256-bit `secret_key` dominates combined entropy. Brute-force offline on an exfiltrated DB is infeasible in both cases.

#### UX implications and mitigations

- **Two credentials to remember** (login + vault password). The onboarding copy makes the separation obvious: "Your account signs you in. Your vault password unlocks your secrets — nobody on our side can read them, including admins. If you forget it, only your emergency kit can recover access."
- **Unlock is per-session, not per-request.** The user types the vault password once when opening the vault; the CryptoKey stays in RAM (non-extractable) until idle-lock or explicit lock. Dozens of page loads per session, one prompt.
- **Biometric unlock on trusted devices.** Native apps and the browser extension wrap `unwrap_key` under the OS keystore (Touch ID, Windows Hello, Android Keystore) so the user doesn't retype the vault password on every launch. The vault password remains the anchor; biometrics only short-circuit the retype on a device that has already seen it. Concretely: after a successful password-based unlock, the client exports `unwrap_key` (the only `extractable: true` use of it), seals the bytes with the OS keystore's biometric-gated key, persists the sealed blob in app storage, and zeros the buffer. On next launch, biometric unlock recovers `unwrap_key`, then the normal §7.2 path resumes from the unwrap step. `kex_priv` and `sig_priv` themselves are never persisted — they are re-derived from `unwrap_key` + the server-fetched wraps every session.

  Considered and rejected: wrapping `kex_priv`+`sig_priv` directly (no meaningful security difference, more state to keep in sync), and wrapping under a fresh device-scoped key that itself wraps a session blob (one extra layer, same attacker outcome on keystore bypass).
- **"Use the same string as your Django password"** is explicitly **allowed** in the UI. The vault password never leaves the client, so there's no new exposure. The only combination we warn against is "weak password + lost secret_key" — fatal regardless of reuse. The minimum-strength gate (§6.1.1) plus the emergency-kit flow cover both failure modes.

#### SSO and the "forgot vault password" story

With SSO on the roadmap, users will naturally expect "my IdP recovered my account, surely my vault is recovered too". **It isn't**, and the UI must make this obvious from day one:

- The vault identity is tied to the Django user, not to the IdP account. Losing the IdP account but keeping the Django user (IdP admin re-provisions with the same email, or a migration tool) retains vault access.
- Losing the vault password means losing the vault contents, regardless of what the IdP or a workspace admin can do. The emergency kit is the only recovery path.
- The onboarding wizard includes a "print/save your emergency kit" step that the user cannot skip before the first vault is unlockable. SSO or not.

This discipline is non-negotiable: the price of "server can't read your data" is "server can't help you recover it".

### 5.2 Hierarchy

```
 ACCOUNT IDENTITY (per user, created at signup)
 ─────────────────────────────────────────────────────────

    vault_password    ──┐
                        ├──► Argon2id ──► AMK  (Account Master Key, 32 B)
    secret_key  ────────┘   (m=64 MiB,          │
                             t=3, p=2,          │
                             salt=32 B)         │
                                                │
                                  HKDF-SHA256 (domain-separated info strings)
                                                │
                                                ▼
                                           unwrap_key
                                          (AES-256-GCM key,
                                           wraps private keys below)

    account_kex_keypair  (X25519 today; X25519+ML-KEM-768 tomorrow)
    account_sig_keypair  (Ed25519 today; Ed25519+ML-DSA-44 tomorrow)

         account_kex_priv  and  account_sig_priv
         are wrapped with unwrap_key:

           wrapped_kex_priv = AEAD(
             key    = unwrap_key,
             iv     = random(12),
             plain  = kex_priv,
             AD     = "v1|account-kex-priv|" + user_uuid
           )

         account_kex_pub is self-signed with account_sig_priv:

           sig_over_kex_pub = Ed25519.sign(
             sig_priv,
             "v1|account-kex-pub|" + user_uuid + "|" + kex_pub
           )


 PER VAULT
 ─────────────────────────────────────────────────────────

    vault_key = CSPRNG(32 B)        ← symmetric, random, NEVER derived from a password

    For each member (owner included):

      wrapped_vault_key_for_member = HPKE.seal(
        recipient_pub = member.account_kex_pub,
        info          = "v1|vault-key|" + vault_uuid + "|" + member.user_uuid,
        plaintext     = vault_key
      )

    share_record = {
      vault_uuid,
      recipient_uuid,
      wrapped_vault_key,
      role,
      expires_at
    }
    share_signature = Ed25519.sign(sharer.sig_priv, canonical_cbor(share_record))


 PER ENTRY (within a vault)
 ─────────────────────────────────────────────────────────

    entry_key = HKDF-SHA256(
      ikm  = vault_key,
      info = "v1|entry-key|" + entry_uuid
    )

    For each sensitive field:
      encrypted_<field> = AEAD(
        key   = entry_key,
        iv    = random(12),
        plain = field_plaintext,
        AD    = "v1|entry-field|" + entry_uuid + "|" + field_name
      )

    entry_metadata = { vault_uuid, entry_uuid, created_at, updated_at,
                       folder_uuid, uris[], icon_ref, key_version, ... }
    entry_metadata_sig = Ed25519.sign(author.sig_priv, canonical_cbor(entry_metadata))
```

### 5.3 Why this shape

- **Vault password only protects `unwrap_key`**, not data. Rotating the password re-wraps the account private keys and `unwrap_key` derivation parameters; **zero vault re-encryption required**.
- **One asymmetric identity per user** (kex + sig). Same identity is used to receive shared-vault keys and to sign everything the user writes. Bob being invited to 5 vaults costs one keypair on his side.
- **Vault keys are random, not derived**. This means rotating a vault key is a self-contained operation independent of any password. It also makes per-vault member sets a pure "who holds a wrap of this key" problem.
- **Per-entry sub-keys via HKDF** cost one HKDF call (negligible) and bound the blast radius of any (IV, key) collision to a single entry. They also let us eventually add "share just this entry" without re-architecting.
- **Everything signed** where it can't be encrypted. A server that tries to silently rewind an entry, change a URI for autofill hijacking, or flip a role in a share record produces an invalid signature. The client refuses it.

---

## 6. Primitive choices

This section is prescriptive: every algorithm, parameter, and encoding is pinned so that two independent implementations interoperate bit-for-bit. If you need to change a choice, bump the version byte.

### 6.0 How the primitives fit together

Before the per-primitive sections, a single synthesized picture of the v1 pipeline:

```
  USER INPUT                     CLIENT-ONLY DERIVATION                  WHAT ENDS UP ON THE SERVER
  ──────────────                 ──────────────────────                  ──────────────────────────

   password ──┐
              ├──► Argon2id ──► AMK (32 B) ──► HKDF("v1|unwrap") ──► unwrap_key (AES-256-GCM)
   secret_key ┘                                                               │
                                                                              │ wraps (AEAD)
                                                                              ▼
                                                                    wrapped_kex_priv
                                                                    wrapped_sig_priv      ────►  stored opaque
                                                                              +
                                  account_kex_keypair (X25519)   ──► kex_pub              ────►  stored plaintext
                                  account_sig_keypair (Ed25519)  ──► sig_pub              ────►  stored plaintext
                                                                 + sig_over_kex_pub       ────►  stored plaintext

   per vault:
     vault_key = CSPRNG(32 B)  ──► HPKE.seal(kex_pub, vault_key)   ──► wrapped_vault_key  ────►  stored opaque

   per entry in a vault:
     entry_key = HKDF(vault_key, "v1|entry-key|<entry_uuid>")
                    │
                    └─► AEAD(entry_key, iv, plaintext_field, AD=...)  ──► encrypted_field ────►  stored opaque
                        (AES-256-GCM)

   signatures on everything that can't be encrypted but must be trusted:
     Ed25519.sign(sig_priv, canonical_cbor(metadata))              ──► metadata_sig       ────►  stored plaintext
```

Five primitives carry the weight:

| Role | Primitive | Why here |
|---|---|---|
| Slow-hash the password | **Argon2id** | Only way to make offline brute-force expensive. 64 MiB memory kills GPU farms. |
| Derive multiple sub-keys from one root | **HKDF-SHA256** | Cheap, standardized, lets us branch by `info` string instead of running Argon2id again. |
| Symmetric encryption of every stored secret | **AES-256-GCM** (v1) | Native in WebCrypto, hardware-accelerated. `additionalData` binds each ciphertext to its context. |
| Wrap a vault key for a specific user's public key | **HPKE** (X25519 + HKDF + AES-GCM) | RFC 9180. Same primitive for self-wrap (owner) and share-wrap (other members). |
| Sign anything plaintext that must not be silently rewritten by the server | **Ed25519** | Fast, small, native in WebCrypto since 2023. |

**Three primitives intentionally NOT used**:

- **aPAKE (OPAQUE, SRP, or Bitwarden-style derived hash)**: not needed. The vault password is never sent to the server, so the server has nothing to verify. Password validity is proven **implicitly** by the successful AEAD decrypt of `wrapped_kex_priv` on the client. This is a permanent choice tied to the project's auth direction (SSO / OpenID Connect — see §5.1).
- **Separate MAC key**: AES-GCM is authenticated on its own. Splitting into encrypt + MAC adds key management without benefit at our sizes.
- **Argon2 inside HKDF**: don't chain KDFs. Argon2id runs once on the password; everything else is fast HKDF off its output.

Everything in §6.1 through §6.8 below is the rigorous version of this picture.

#### Dependency policy — no home-rolled crypto

Every primitive in the table above is provided by an audited, maintained library. Implementing crypto primitives ourselves — even "simple" ones like ECDH wrapping or canonical CBOR — is a well-known footgun: you reimplement a protocol whose edge cases (IV reuse, tag truncation, malleable encoding, non-constant-time paths) have been documented in incident reports for a decade, and you lose the benefit of test-vector validation against a reference.

| Primitive | Library (browser) | Library (server) | Size (gzipped) |
|---|---|---|---|
| Argon2id | **`hash-wasm`** (WASM, exposes `secret`/K param) | `argon2-cffi` via `core()` (low-level, exposes `secret`) — used only for test-vector generation | ~40 KB |
| HKDF, AES-GCM, X25519 ECDH, Ed25519, SHA-256 | native `crypto.subtle` (WebCrypto) | `cryptography` (Python) | 0 |
| HPKE | `@hpke/core` + `@hpke/dhkem-x25519` | `pyhpke` | ~15 KB |
| CBOR deterministic | `cbor-x` | `cbor2` | ~20 KB |
| Password strength | `@zxcvbn-ts/core` + `@zxcvbn-ts/language-common` (lazy-loaded on password dialog only) | — (client-only) | ~25 KB, **off the main bundle** |

Total browser-side non-native code on the **main bundle**: **~75 KB gzipped** (Argon2id WASM + HPKE + CBOR). The strength estimator (~25 KB) is loaded on demand. Acceptable for a vault UX.

**If a library choice changes** (new major version, fork, or replacement), the primitive it implements does not change — the wire format is fixed by §6.10 and the primitive parameters in the subsections below. Two implementations using different libraries but matching the spec remain bit-compatible.

### 6.1 Password KDF — Argon2id

Used for: deriving `AMK` from `(vault_password, secret_key, salt)` at signup, login, and password rotation.

**Algorithm**: Argon2id v1.3 (0x13), RFC 9106.

**Parameters (v1)**:

| Parameter | Value | Why |
|---|---|---|
| Variant | Argon2id | Side-channel resistance (Argon2i) + GPU resistance (Argon2d). OWASP / RFC 9106 default. |
| Memory cost `m` | 65 536 KiB (64 MiB) | OWASP 2024 floor is 19 MiB. 64 MiB raises GPU-farm cost by ~3×, still fits mobile Safari's ~1 GB JS heap. Costs ~300 ms on M3, ~800 ms on Snapdragon 8 Gen 2. |
| Time cost `t` | 3 | Combined with 64 MiB, keeps total under ~1 s on median devices. Below t=2 leaves TMTO attacks too cheap. |
| Parallelism `p` | 2 | `p=1` under-uses modern browser WASM threads; `p=4` hurts low-end single-core devices. `p=2` is the safe median. |
| Output length | 32 bytes | Exactly the AMK size. No truncation. |
| Salt length | 32 bytes | Cryptographic salt, unique per user. |
| Associated data | Empty | Not used. |
| Secret key (`K`) | `secret_key` (32 bytes, raw) | RFC 9106's native pepper input. See input construction below. |

**Salt construction**: 32 bytes server-generated at signup via `os.urandom(32)`. Fetched back on every login. **Not derived** from user ID or email — those rotate, the salt doesn't.

**Input construction** (this matters — reproducibility depends on exact byte layout):

```
password_input = UTF-8(NFC-normalize(vault_password))

AMK = Argon2id(
  password = password_input,
  salt     = salt,             # 32 bytes, fetched from server
  secret   = secret_key,       # 32 bytes, K parameter (pepper) — never sent to server
  m        = 65536, t = 3, p = 2,
  output_len = 32,
  version  = 0x13
)
```

- `NFC-normalize` prevents `"café"` (precomposed) vs `"café"` (composed) divergence across input methods.
- `secret_key` is passed via Argon2id's **native `K` parameter** (RFC 9106 §3.1), the input designed for keyed-hash / pepper use. Folding into the salt with a SHA-256 pre-hash was considered and rejected: `K` is covered directly by the RFC's security analysis, doesn't depend on a hash decision, and decouples the salt size from the `secret_key` size if either ever rotates.

**Library obligations**:
- Browser side: **`hash-wasm`** — its `argon2id({ password, salt, secret, ... })` exposes `secret` directly. `argon2-browser` does NOT expose `K` cleanly and must be avoided here. (`hash-wasm` is also better maintained as of 2026.)
- Reference side (test-vector generation only — the server never derives `AMK`): **`argon2-cffi`** via the low-level `core()` interface, which lets us populate the `secret` / `secretlen` fields of the underlying `argon2_context`. The standard `hash_secret_raw()` function does **not** expose `K` and must not be used for AMK derivation.

If a future Argon2id library landed without `K` support, switch libs rather than fold back into salt — the KDF version token in `kdf_params` already provides a migration path, but we should not have to use it for a library-shaped reason.

**Library**:
- Client: `argon2-browser` WASM (v1.19+), or `hash-wasm` — both support v1.3.
- Server: **never** touches the password or AMK. The server stores `kdf_params` and `salt` verbatim and returns them on demand, but computes nothing over the password.

#### 6.1.1 Minimum password strength (client-enforced)

Argon2id is hard but not magic. A 4-character password with any KDF parameters falls quickly; the `secret_key` dominates combined entropy (see §5.1) but only if the user actually keeps their emergency kit. We therefore enforce a **strength floor** at the point the user sets or rotates the vault password.

**Rules (v1)**:

| Check | Enforcement |
|---|---|
| Minimum length | 12 characters (Unicode codepoints, post-NFC) |
| Strength score | ≥ 3 (on 0-4 scale), computed client-side via **`@zxcvbn-ts/core`** with the English `language-common` pack only (no full multilingual dictionaries — adds ~25 KB instead of ~150 KB) |
| Bundle loading | **Lazy-loaded only on the password set/change dialog**, never in the main bundle. The dialog imports the strength estimator dynamically; vault unlock and entry pages do not pay this weight. |
| Breach denylist | **HIBP k-Anonymity API** (`https://api.pwnedpasswords.com/range/<5-char-prefix-of-sha1>`). One HTTPS GET per password attempt; only the first 5 chars of `SHA-1(password)` leave the device. No bloom filter shipped client-side (out-of-date the day it ships and bloated to embed). On network failure: warn but do not block (privacy/availability over completeness). |
| Compositional rules | **Not used.** No "must have 1 uppercase + 1 digit + 1 symbol" — those correlate poorly with real entropy and push users toward predictable patterns. |

**UX**:
- Strength meter shows zxcvbn's `score` (0-4) with a label ("very weak" → "very strong") and a `crack_times_display` estimate.
- Button stays disabled until the floor is met; re-enabled in real time as the user types.
- If the user pastes a password that appears in the HIBP denylist, show an explicit "this password has appeared in known breaches" error rather than a generic strength warning.

**Rotation**: same floor applies to password rotation. Server does not enforce the floor (it can't — it never sees the password); the client applies it before driving the rotation flow. A user with a pre-floor weak password is prompted to rotate on next unlock.

This strength gate is **client-side only**. The server has no way to verify it and doesn't try — that's fine, the server is not the adversary we're defending against here (it's the user's future self losing their secret_key).

**Migration slot**: `kdf_params` stored as `{"algo": "argon2id", "v": "1.3", "m": 65536, "t": 3, "p": 2}`. On a future upgrade (e.g. `m=131072` for defense against GPU progress), bump the version token, store both, re-derive on next password rotation.

### 6.2 HKDF — sub-key derivation

Used for: deriving `unwrap_key` from `AMK`, and `entry_key` from `vault_key`. **Never** for password stretching (Argon2id's job).

**Algorithm**: HKDF-SHA-256 (RFC 5869). Extract-then-Expand.

**Usage pattern**:

```
output = HKDF-SHA-256(
  ikm       = <input key material>,
  salt      = 32 × 0x00,           # 32 zero bytes — see rationale below
  info      = <domain separator, UTF-8, ASCII only>,
  length    = 32 bytes
)
```

**Why `salt = 32 zero bytes`**: RFC 5869 §3.1 states the salt is optional when the IKM is already high-entropy. `AMK` comes out of Argon2id (uniformly random); `vault_key` comes out of `CSPRNG(32)` (uniformly random). HKDF-Extract with a zero salt is safe in both cases. **Do not** pass user-controlled salt here — that's what the KDF context is for.

A per-user HKDF salt was considered as defense-in-depth and rejected: the IKM is genuinely high-entropy in both call sites, RFC 5869 covers this case explicitly, and the storage/migration cost of a per-user salt buys no defendable benefit at this entropy level. If a future protocol revision needs domain-separated sub-keys per user, they belong in the `info` parameter, not in the salt.

**Canonical catalog of `info` strings** (authoritative — this list IS the contract):

| Source | `info` | Purpose |
|---|---|---|
| AMK | `"v1\|unwrap"` | Derives `unwrap_key` (AES-256-GCM key wrapping account privates) |
| AMK | `"v1\|audit-hmac"` | Derives HMAC key for local audit-log chaining (optional feature) |
| vault_key | `"v1\|entry-key\|"` + entry_uuid (36-char RFC 4122 lowercase with hyphens) | Derives per-entry symmetric key |
| HPKE shared secret | handled internally by HPKE | Not manually derived |

**Every `info` string starts with a version prefix**. Bumping the top-level version invalidates all sub-keys in a controlled way.

**Separator**: ASCII `|` (0x7C). Chosen because it never appears in UUIDs, version tokens, or role names. If any context field could ever contain `|`, use length-prefixed encoding instead — but today none can.

**Encoding**: UTF-8 of ASCII strings only. No Unicode in `info` strings. No trailing newline. No trailing `|`.

### 6.3 Symmetric AEAD

Used for: every encrypted field, every wrapped key at rest.

**Primary (v1)**: AES-256-GCM (FIPS 197 + SP 800-38D).

| Parameter | Value |
|---|---|
| Key size | 256 bits |
| IV size | 96 bits (12 bytes) |
| IV construction | **Random per operation** via `crypto.getRandomValues` |
| Tag size | 128 bits (16 bytes) — default, never truncated |
| AD | Structured context string (see §6.4) |
| Max messages per key | 2³² before risk of IV collision becomes > 2⁻³² |

**IV collision math**: with random 96-bit IVs, the birthday bound is 2⁴⁸ messages before collision probability exceeds 2⁻³². NIST SP 800-38D caps it at 2³² per key to stay well clear. For the `vault_key`, this translates to 4 billion encryptions before rekey — practically unlimited for a personal vault. For `entry_key` (per-entry), the budget is per-entry, so essentially infinite.

**Key commitment caveat**: AES-256-GCM is **not** key-committing. An attacker with a chosen-key-cipher-text-pair (e.g. malicious content included in a shared attachment) could craft ciphertext that decrypts to different plaintexts under two different keys. Mitigations in this design:
1. Keys are never shared cross-user without envelope wrapping (HPKE). The attacker never gets to choose a key.
2. AD binds each ciphertext to its entry UUID + field name — cross-context substitution fails.
3. If key-commitment becomes critical (specific attacks against password managers land), migrate to AEGIS-256 or add commitment via the "Padme" construction (hash of key into the AD). Already covered by the version byte in the ciphertext format.

**Future (v2)**: XChaCha20-Poly1305 (RFC 8439 + IETF draft). Reasons to move:
- 192-bit nonce → no random collision worry even at absurd message counts.
- Constant-time on platforms without AES hardware (mobile ARM without AES extensions).
- Cleaner WASM implementations than constant-time AES-GCM software implementations.

Trade-off: +~80 KB WASM payload vs. native WebCrypto AES-GCM. Ship when the first migration lands naturally (e.g. with Argon2id upgrade).

### 6.4 Associated-data (AD) construction

AD is **non-secret, non-redundant, deterministic** context that binds a ciphertext to its usage.

**Format**: UTF-8 of ASCII string, `|`-separated, version-prefixed.

**Canonical catalog**:

| Ciphertext | AD |
|---|---|
| Account privkey wrap (AES-GCM) | `"v1\|account-kex-priv\|"` + user_uuid |
| Account sigkey wrap (AES-GCM) | `"v1\|account-sig-priv\|"` + user_uuid |
| Entry field (AES-GCM) | `"v1\|entry-field\|"` + entry_uuid + `"\|"` + field_name |
| Signed metadata (Ed25519 payload) | `"v1\|metadata\|"` + object_type + `"\|"` + object_uuid |

Wrapped vault keys are not in this table — they are encrypted via HPKE, whose context binding lives in the `info` parameter (§6.5), not in an AEAD AD.

**UUID formatting**: lowercase RFC 4122 with hyphens. Example: `e4c1f9d2-8a6b-7c4e-9d1f-2e5a6b3c4d8f`. **Never** swap for bytes, base64, or uppercase — the AD must bit-match on decrypt.

**Field names**: fixed lowercase ASCII identifiers. The authoritative list is:
`name`, `username`, `password`, `totp`, `notes`, `custom:<field_id>` where `<field_id>` is a URL-safe slug chosen client-side at field creation.

An AD mismatch on decrypt produces an AEAD authentication failure. The UI must **never** retry decryption with a different AD on failure — it must surface the tamper.

### 6.5 Asymmetric — HPKE for key wrapping

Used for: wrapping `vault_key` for self and for other members.

**Suite (v1)**: `DHKEM(X25519, HKDF-SHA256)` + `HKDF-SHA256` + `AES-256-GCM`.

| Field | Value | HPKE ID |
|---|---|---|
| KEM | DHKEM(X25519, HKDF-SHA256) | `0x0020` |
| KDF | HKDF-SHA256 | `0x0001` |
| AEAD | AES-256-GCM | `0x0002` |
| Mode | `mode_base` (unauthenticated) | `0x00` |

(RFC 9180 §7.3 IANA registry: `0x0001` is AES-128-GCM, `0x0002` is AES-256-GCM, `0x0003` is ChaCha20-Poly1305. Double-check against the library's enum before wiring any byte-level wire layout — a one-off here desynchronizes every share.)

**Why `mode_base` not `mode_auth`**: sender authentication happens at a higher layer (Ed25519 signature on the share_record). `mode_auth` would bind to sender's static key — if the sender rotates their kex key later, old ciphertexts become orphaned. Keeping auth at the signature layer is cleaner.

**Context `info`**: `"v1|vault-key|" + vault_uuid + "|" + recipient_uuid` (UTF-8 ASCII). **Same string for owner self-wrap and for a share to another member** — wrapping a symmetric key for a pub key is one operation, not two. `recipient_uuid` is the user the wrap is addressed to (the owner's own user_uuid for a self-wrap, the member's user_uuid for a share).

**HPKE `aad` parameter**: empty. Context binding goes in `info` (bound into the KEM-derived shared secret), not in the AEAD's additional data. Keeping `aad` empty prevents a class of bugs where two implementations disagree on the AAD encoding.

**On-wire format**: HPKE's own framed output (`enc` || `ciphertext`). No custom envelope — HPKE libraries produce and consume this natively.

**Library**: `@hpke/core` + `@hpke/dhkem-x25519` + `@hpke/chacha20poly1305` (for the future PQ slot). Python side: `hpke-py`.

**PQ migration slot**: When the IETF finalizes hybrid-KEM IDs for ML-KEM / X25519+ML-KEM in HPKE, we bump to that ID in a v2.

> **TODO — pick a PQ combiner before implementing v2.** Naive concat of the two KEM shared secrets is not safe in general; use a combiner of the form `H(ss_ecdh || ct_ecdh || pk_ecdh || ss_pq || ct_pq || pk_pq)` per the current `draft-ietf-hpke-pq` (or its successor). Track the draft — IDs have moved between revisions, do not pin a number in this doc until the RFC lands.

### 6.6 Signatures

Used for: signing account_kex_pub, share_records, entry_metadata, audit-log entries.

**Algorithm (v1)**: Ed25519 (pure, RFC 8032 §5.1).

| Parameter | Value |
|---|---|
| Public key | 32 bytes (compressed Edwards y + sign bit) |
| Private key (seed) | 32 bytes |
| Signature | 64 bytes |
| Hash | SHA-512 (prescribed by Ed25519) |
| Context | **empty** — use `Sign`, not `SignCtx` |

**Why empty context**: Ed25519ctx/Ed25519ph are NOT universally supported (WebCrypto spec stabilized pure Ed25519 only). Keep to pure. Domain separation goes in the signed **payload**, not in the context parameter. Every signed payload starts with a CBOR map `{"v": 1, "type": "...", ...}`.

**Canonical payload**: CBOR deterministic (see §6.9), then sign.

**Verification protocol**:
1. Decode the CBOR payload.
2. Check `v == 1` (reject unknown versions).
3. Check `type` matches the expected context (e.g. a share_record verifier rejects signatures over entry_metadata payloads).
4. Re-canonicalize the decoded payload and compare byte-for-byte (catches maliciously-encoded-but-parseable CBOR).
5. `Ed25519.verify(pubkey, canonical_bytes, signature)`.

**PQ migration slot**: Ed25519 + ML-DSA-44 hybrid. Wire format: `Ed25519_sig || ML-DSA-44_sig`. Both must verify. Acceptable cost: ~2.4 KB extra per signature.

### 6.6.1 Identity trust establishment

The whole integrity story rests on one question: how does a client know that a public key it fetched from the server is actually the one the claimed owner published? The server can swap any public blob. Signatures over swapped keys mean nothing unless the verifying key itself is trusted.

The answer has two anchors, one per party:

**For your own identity**, the anchor is your password. After a successful unlock, the client re-derives `unwrap_key`, decrypts `wrapped_sig_priv`, and can independently compute `sig_pub`. It then checks that the server-returned `account_sig_pub` equals the locally computed one. If the server swapped it, AEAD still decrypts (attacker doesn't have `unwrap_key`, so they couldn't have produced a matching wrap), so really the swap would be on `sig_pub` alone — caught here. The client then re-verifies `sig_over_kex_pub` with the local `sig_pub`. A swap of `kex_pub` is caught.

**For someone else's identity** (when you receive an invitation from Bob), the anchor is out-of-band fingerprint verification:

```
safety_number = base32(SHA-256(alice.sig_pub || bob.sig_pub))[:60 bits]
              = 12 characters, grouped 3-3-3-3 for readability
```

First-contact flow:
1. Client fetches `bob.sig_pub`, `bob.kex_pub`, `bob.sig_over_kex_pub`.
2. Client verifies `sig_over_kex_pub` with `bob.sig_pub`. (Checks consistency, not authenticity.)
3. Client displays `bob.sig_pub`'s fingerprint as a 12-char safety number.
4. User verifies it with Bob over an out-of-band channel (QR on screen, voice, in-person).
5. On success, the client pins `bob.sig_pub` as "verified" in its local identity store.
6. On subsequent fetches, `bob.sig_pub` must match the pinned value; `bob.kex_pub` can change (rotation), but the new `sig_over_kex_pub` must still verify under the pinned `bob.sig_pub`.

**Sig-keypair rotation**: when Bob rotates `sig_keypair` (new device, suspected compromise, periodic rotation), he publishes a **rotation attestation** signed by the **old** `sig_priv`:

```
rotation_payload = canonical_cbor({
  "v": 1, "type": "sig_rotation",
  "user_uuid": bob.user_uuid,
  "old_sig_pub": <bytes>,
  "new_sig_pub": <bytes>,
  "new_kex_pub": <bytes>,
  "new_sig_over_kex_pub": <64 bytes>,
  "created_at_ms": <uint>,
  "nonce": <bytes[16]>
})
rotation_sig = Ed25519.sign(old_sig_priv, rotation_payload)
```

A peer who has `old_sig_pub` pinned can verify the attestation and automatically pin `new_sig_pub`. If Bob lost `old_sig_priv` (he has no way to sign the attestation), peers must re-verify the new fingerprint out of band. The UI surfaces this as "Bob's identity changed — verify his new safety number before sharing".

**TOFU risk acknowledgement**: a user who skips the first-time fingerprint prompt is trusting the server to serve the right key. If the server is an active adversary during that first fetch, Bob's "identity" is now the server's — all future shares to "Bob" go to the attacker. The UI MUST make the unverified state visible (yellow/amber badge, no implicit green) until fingerprint verification occurs. Do not soften this for UX.

**Enforcement level (chosen — contextual)**:

| Context | Action on first-time identity |
|---|---|
| Recipient is in the same workspace / authenticated by the same IdP | **Soft warn**: share is accepted, persistent yellow "unverified identity" badge on every entry from this source until fingerprint is verified out of band. Inspired by Signal's model. The IdP already authenticated the peer; the identity-swap risk is bounded to a server-vs-IdP collusion. |
| Recipient is external (different workspace / no shared IdP authentication / cross-org invite) | **Hard block**: client refuses to decrypt the wrapped vault key until the fingerprint is confirmed via an out-of-band channel (QR scan in person, voice call, separate messaging app). No "skip and warn" option for this path. |

A workspace-scoped share is therefore one click ("accept invitation") for the typical company case, with a persistent reminder until users get around to verifying. A cross-org share forces the verification step before any plaintext is reachable, which is the only sound default when no shared trust anchor exists.

**Implementation hook**: the determination of "same workspace" vs "external" must be made client-side from data that the client itself trusts (e.g., the user's own workspace membership embedded in the Django session payload, cross-checked against the recipient's user_uuid in a workspace-membership listing). A server-asserted "same workspace, trust me" boolean is not sufficient — it would let a malicious server downgrade hard-block to soft-warn for any target.

### 6.7 Server-side password verification — not used

The vault password is a client-only secret. It is never transmitted to the server in any form — plaintext, hash, or derivative. Validity is proven **implicitly** by whether `AES-256-GCM.decrypt(unwrap_key, wrapped_kex_priv, AD=…)` succeeds on the client after the user types their password. If the AEAD tag validates, the password was correct; if it fails, it was wrong. The server returns opaque blobs on request (authenticated by the Django session cookie) and has no visibility into whether the unlock attempt succeeded.

Because of this, **no aPAKE primitive is needed**. No OPAQUE, no SRP, no Bitwarden-style derived-hash verifier, no server-side password proof. This is a permanent design choice tied to the project's auth direction (SSO/OpenID Connect, see §5.1): there is no Django login password for the vault to unify with, and there will not be one.

Login identity (Django session) and vault access (vault password) are therefore independently managed and independently rotatable.

### 6.8 CSPRNG, constant-time, side channels

**Randomness**:
- Browser: `window.crypto.getRandomValues(new Uint8Array(n))`. Never `Math.random()`.
- Server: `secrets.token_bytes(n)` or `os.urandom(n)`. Never `random.*`.
- Dedicated helper: wrap these in `csprng.rand_bytes(n)` and `assert len(out) == n` — defense against partial fills.

**Constant-time operations**:
- All signature verification (Ed25519) is constant-time by library guarantee.
- `check_password` (Django) uses `constant_time_compare`. OK.
- **Our own code**: when comparing tags, UUIDs in AD, safety numbers — use `hmac.compare_digest` (Python) / `crypto.timingSafeEqual` (Node) / manual XOR-sum (browser, no primitive). Never `==`.

**Side channels we accept**:
- Memory timing / cache timing in the browser — browsers don't expose counter-resolution timers anymore post-Spectre; practical side-channel extraction is out of scope.
- Server CPU time observable via response timing — mitigated by rate-limiting + returning 404 uniformly for "not found" / "no access" cases.

### 6.9 Canonical serialization — CBOR

Used for: anything signed.

**Standard**: RFC 8949 §4.2.2 "Core Deterministic Encoding Requirements" + §4.2.3 extensions for our use:

1. Integers: encoded in shortest form (no leading zero bytes).
2. Floats: **forbidden**. Any need for "a number" must be an integer (timestamps in milliseconds, etc.).
3. Maps: keys sorted by bytewise lexicographic order **of their CBOR encoding** (not by string compare).
4. Lengths: definite (not `0x5F...0xFF` indefinite).
5. No tags (except `0xD9 0x0100` for timestamps when strictly needed — we prefer raw ms since epoch as uint).
6. Strings: UTF-8, NFC-normalized.
7. Keys: ASCII short strings (`"v"`, `"type"`, `"vault_uuid"`, etc.).

**Library**:
- Client: `cbor-x` configured with `{ useRecords: false, mapsAsObjects: false, largeBigIntToFloat: false }` + a post-encode check that re-decoding yields the same structure.
- Server: `cbor2` (Python) with `canonical=True`.

**Signed payload template**:
```
{
  "v":       1,
  "type":    "share_record" | "entry_metadata" | "account_kex_binding" | ...,
  "created_at_ms": <uint>,
  "author_uuid":   <bytes[16]>,
  "nonce":         <bytes[16]>,          # fresh per payload; prevents replay
  ...type-specific fields...
}
```

Decoders **reject** any payload that doesn't re-canonicalize to the exact signed bytes.

### 6.10 Ciphertext wire format — exact byte layout

This layout describes **raw AEAD ciphertexts** produced directly by the client — the account-private-key wraps and the entry-field ciphertexts. It does **not** describe HPKE-wrapped vault keys: those use HPKE's own framed output (`enc || ciphertext`, §6.5) and carry their algorithm IDs inside the HPKE header.

```
Offset  Size (B)  Field           Values
──────  ────────  ──────────────  ──────────────────────────────────────────
0       1         format_version  0x01  (bump for any layout change)
1       1         aead_id         0x01 = AES-256-GCM
                                   0x02 = XChaCha20-Poly1305
                                   0x03 = AEGIS-256 (reserved)
2       1         kdf_id          0x00 = key used directly (no per-ciphertext KDF)
                                   0x01 = HKDF-SHA256 (per-entry sub-key derivation)
3-4     2         key_version     big-endian uint16, scope depends on context:
                                   - entry-field ciphertext: version of the vault_key
                                     that derived the entry_key
                                   - account privkey wrap:  0x0000 (N/A — wraps
                                     rotate on password change, not on key_version)
5       1         iv_len          12 for AES-GCM, 24 for XChaCha
6       iv_len    iv              CSPRNG bytes
6+iv_len N        ciphertext_tag  ciphertext concatenated with 16-byte tag
```

**`kdf_id` semantics by ciphertext type:**

| Ciphertext type | `kdf_id` | Why |
|---|---|---|
| Entry field | `0x01` (HKDF-SHA256) | Key used = HKDF(vault_key, "v1\|entry-key\|<entry_uuid>"). Per-entry isolation. |
| Account privkey wrap (`wrapped_kex_priv`, `wrapped_sig_priv`) | `0x00` | Key used = `unwrap_key` directly. No per-ciphertext KDF needed. |

Then base64url-encoded (without padding) for JSON transport. Total overhead: 6 + iv_len + 16 = **34 bytes (AES-GCM)** or 46 bytes (XChaCha) per encrypted value. Acceptable for password-sized plaintexts.

**Decoder rules** (non-negotiable):
- `format_version` byte `!= 0x01` → reject with error code `UNSUPPORTED_VERSION`. Do not attempt any parsing.
- `iv_len` must match the declared `aead_id`. Mismatch → reject.
- AEAD open failure → surface loudly, do not swallow, do not return partial plaintext.
- `key_version` that the client doesn't hold a wrap for → fetch the wrap from the server and retry; if still missing, surface.

> Note: this field is `format_version` — the wire-layout version. It is distinct from `entry_version` in `entry_metadata` (§7.4), which is the entry **content schema** version (fields added/removed). The two can move independently; don't conflate them.

### 6.11 Performance budget (reference targets, 2025 median device)

Targets calibrated on: M3 MacBook Air, Snapdragon 8 Gen 2 (Pixel 8), iPhone 15.

| Operation | Target | Mechanism |
|---|---|---|
| Argon2id (m=64 MiB, t=3, p=2) | 300-800 ms | WASM Argon2 |
| HKDF | < 1 ms | WebCrypto native |
| AES-256-GCM encrypt/decrypt (1 KB) | < 1 ms | WebCrypto native (AES-NI on most CPUs) |
| HPKE seal/open (32 B payload) | < 5 ms | @hpke/core |
| Ed25519 sign | < 1 ms | WebCrypto native (since 2023) |
| Ed25519 verify | < 2 ms | WebCrypto native |
| Full unlock flow (Argon2id + AES-GCM unwrap + decrypt 1 000 entries) | < 2 s | End-to-end |

**Alert thresholds** (monitored client-side):
- Argon2id > 2 s → device likely underpowered, surface "choose a shorter list of vaults or reduce KDF cost".
- Total unlock > 5 s → something is wrong; emit a `slow_unlock` event to the client error log.

### 6.12 Test vectors (required for any implementation)

The implementation ships with test vectors for each primitive to ensure cross-language parity:

```
# AMK derivation
password:   "Tr0ub4dor&3"
secret_key: hex"0102...20" (32 bytes)
salt:       hex"A1B2...20" (32 bytes)
expected_AMK: hex"<known>"

# HKDF info="v1|unwrap"
input_key: AMK (from above)
expected_unwrap_key: hex"<known>"

# AES-256-GCM
key:   hex"...32 bytes"
iv:    hex"...12 bytes"
ad:    "v1|entry-field|<uuid>|password"
plain: "hunter2"
expected_ciphertext: hex"<known>"

# HPKE seal
sender_sk: <X25519 priv>
recipient_pk: <X25519 pub>
info: "v1|vault-key|<uuid>|<uuid>"
plain: <32 bytes>
expected_enc: <known>
expected_ciphertext: <known>

# Ed25519
sk: <known>
msg: canonical CBOR of { "v":1, "type":"test", ... }
expected_sig: <known>
```

These vectors are generated once via a reference implementation (Python, `cryptography` + `argon2-cffi` + `pyhpke`) and committed to `tests/crypto_vectors.json`. Every new implementation (JS, mobile, CLI) must pass them.

---

## 7. Detailed flows

### 7.1 Vault onboarding (first vault on this account)

Runs once, when the user sets up their first vault on an already-authenticated Django account. Subsequent vaults reuse the account identity established here.

```
CLIENT                                                          SERVER
──────                                                          ──────
[user already logged into Django — session cookie present]

[show "set vault password" dialog; user enters vault_password]

generate secret_key = CSPRNG(32)       ────── (never leaves client) ─────► (SHOWN TO USER
                                                                           in emergency kit PDF)

POST /api/v1/passwords/account/init { }
                                      ─────────────────────────────────►
                                                                         salt = os.urandom(32)
                                                                         store (user_id, salt,
                                                                                state="pending")
                                      ◄────────────────────────────────
                                                                         200 {user_uuid, salt}

AMK        = Argon2id(password=vault_password, salt=salt, secret=secret_key,
                      m=65536, t=3, p=2, len=32)
unwrap_key = HKDF-SHA256(ikm=AMK, salt=32×0x00,
                         info="v1|unwrap", len=32)

account_kex_keypair = X25519.keygen()                 # used for sharing
account_sig_keypair = Ed25519.keygen()                # used for metadata

wrapped_kex_priv = AES-256-GCM(
  key=unwrap_key, iv=CSPRNG(12), plain=kex_priv,
  AD="v1|account-kex-priv|" + user_uuid
)
wrapped_sig_priv = AES-256-GCM(
  key=unwrap_key, iv=CSPRNG(12), plain=sig_priv,
  AD="v1|account-sig-priv|" + user_uuid
)
sig_over_kex_pub = Ed25519.sign(
  sig_priv,
  "v1|account-kex-pub|" + user_uuid + "|" + kex_pub
)

POST /api/v1/passwords/account/finalize {
  kdf_algo:  "argon2id",
  kdf_params: {m: 65536, t: 3, p: 2},
  account_kex_pub, account_sig_pub,
  wrapped_kex_priv, wrapped_sig_priv,
  sig_over_kex_pub
}                                     ─────────────────────────────────►
                                                                         validate presence, shapes
                                                                         store (state="active")
                                      ◄────────────────────────────────
                                                                         201

[client wipes vault_password, secret_key, AMK from memory
 immediately after the emergency kit has been shown/printed.
 unwrap_key and private keys stay in RAM, non-extractable,
 until idle-lock or explicit lock.]
```

The emergency kit PDF contains: email, secret_key (Base32 with checksum digits, human-transcribable), server URL, generation date, and a clear warning: "without this and your vault password, your data is unrecoverable — nobody, including the server operator, can unlock it".

**PDF generation MUST be 100 % client-side** (e.g. `jsPDF` or `pdf-lib`). The raw `secret_key` must never be POSTed, uploaded, logged, or serialized into any server-bound request. Any server-rendered PDF pipeline (server-side puppeteer, Print via backend) would leak `secret_key` and breaks zero-knowledge — this is a hard constraint, not a preference. Include a CI guard (static check) that `secret_key` literals never appear in request bodies.

**What the server sees**: public keys (plaintext), wrapped private keys (opaque), salt, KDF parameters, one self-signature over the kex pub. What it never sees: the vault password, the secret_key, the AMK, the unwrap_key, the account private keys.

### 7.2 Vault unlock

Runs every time the user opens the vault in a new browser session (after idle-lock, after page reload, etc.). The user is already authenticated to Django via session cookie.

```
CLIENT                                                          SERVER
──────                                                          ──────
[Django session cookie present — identity already established]
[user types vault_password; secret_key retrieved from local
 device storage (trusted device) or emergency kit (new device)]

GET /api/v1/passwords/account/envelope
                                      ─────────────────────────────────►
                                                                         authz: caller is the owner
                                                                         return opaque blobs
                                      ◄────────────────────────────────
                                                                         200 {
                                                                           salt, kdf_params,
                                                                           wrapped_kex_priv,
                                                                           wrapped_sig_priv,
                                                                           account_kex_pub,
                                                                           account_sig_pub,
                                                                           sig_over_kex_pub
                                                                         }

# 1. Re-derive the unwrap_key
AMK        = Argon2id(password=vault_password, salt=salt, secret=secret_key,
                      m=kdf_params.m, t=kdf_params.t, p=kdf_params.p, len=32)
unwrap_key = HKDF-SHA256(ikm=AMK, salt=32×0x00, info="v1|unwrap", len=32)

# 2. Unwrap the private keys.
#    If the vault_password is wrong, AES-GCM tag validation FAILS here
#    — this is how we know the password was incorrect. No server round-trip needed.
try:
    kex_priv = AES-256-GCM.decrypt(
      key=unwrap_key, ciphertext=wrapped_kex_priv,
      AD="v1|account-kex-priv|" + user_uuid
    )
    sig_priv = AES-256-GCM.decrypt(
      key=unwrap_key, ciphertext=wrapped_sig_priv,
      AD="v1|account-sig-priv|" + user_uuid
    )
except AEADAuthenticationError:
    show "incorrect vault password" and re-prompt; no server call.

# 3. Verify the self-signature over kex_pub (detects server key swap).
assert Ed25519.verify(
  account_sig_pub, sig_over_kex_pub,
  "v1|account-kex-pub|" + user_uuid + "|" + account_kex_pub
)

[vault_password, AMK wiped from memory.
 unwrap_key, kex_priv, sig_priv live in RAM (CryptoKey, extractable=false)
 until idle-lock or explicit lock.]
```

**Key property**: the server has no role in deciding whether the password was right. It serves the blobs to the authenticated Django user, and that's it. A wrong password surfaces as a local AEAD decryption failure — never as a server response.

**Rate-limiting**: the server should still rate-limit `GET /account/envelope` (e.g. 20/hour/user) to slow exfiltration-then-offline-attack if a session cookie is compromised. The Argon2id cost and 256-bit secret_key are the primary defense against offline brute-force; rate-limiting is defense-in-depth.

### 7.3 Vault creation

```
CLIENT                                                          SERVER
──────                                                          ──────

vault_uuid = UUIDv7()
vault_key = CSPRNG(32)

wrapped_for_owner = HPKE.seal(
  recipient = my.account_kex_pub,
  info      = "v1|vault-key|" + vault_uuid + "|" + my.user_uuid,
  plaintext = vault_key
)

metadata = {
  vault_uuid, owner_uuid: my.user_uuid,
  name: encrypted_name, icon, color,
  created_at, kdf_version: "v1"
}
metadata_sig = Ed25519.sign(my.sig_priv, canonical_cbor(metadata))

POST /api/v1/passwords/vaults {
  vault_uuid,
  owner_wrapped_key: wrapped_for_owner,
  metadata, metadata_sig
}                                     ─────────────────────────────────►
                                                                         verify metadata_sig
                                                                         against owner.sig_pub
                                                                         store
                                      ◄────────────────────────────────
                                                                         201

[vault_key lives in memory non-extractable for this session;
 wiped on lock]
```

### 7.4 Entry write

```
CLIENT
──────

entry_uuid = UUIDv7()
entry_key  = HKDF(vault_key, "v1|entry-key|" + entry_uuid)

For each sensitive field f in {name, username, password, totp, notes, ...}:
  encrypted_<f> = AEAD(
    entry_key, random_iv, plain_f,
    AD = "v1|entry-field|" + entry_uuid + "|" + f
  )

metadata = {
  vault_uuid, entry_uuid,
  created_at, updated_at,
  folder_uuid, icon_ref,
  uris: [...],                    # plaintext if user opted-in, ciphertext otherwise
  key_version: 1, entry_version: 1
}
metadata_sig = Ed25519.sign(my.sig_priv, canonical_cbor(metadata))

POST /api/v1/passwords/entries {
  entry_uuid, vault_uuid,
  encrypted_name, encrypted_username, encrypted_password,
  encrypted_totp, encrypted_notes, encrypted_fields_extra,
  metadata, metadata_sig
}

SERVER
──────

verify metadata_sig against author.sig_pub
check authz: caller has write access to vault_uuid
store
```

### 7.5 Entry read

```
CLIENT
──────

GET /api/v1/passwords/entries?vault=<vault_uuid>

response: list of {
  entry_uuid, encrypted_*, metadata, metadata_sig
}

For each entry:
  verify metadata_sig against metadata.author.sig_pub
  (if sig_pub unknown → fetch from server, verify its self-signature,
   prompt fingerprint check if new identity)

  entry_key = HKDF(vault_key, "v1|entry-key|" + entry_uuid)

  For each field:
    plain_<f> = AEAD.open(
      entry_key, encrypted_<f>,
      AD = "v1|entry-field|" + entry_uuid + "|" + f
    )
    if AEAD.open fails:
      raise tamper_detected(entry_uuid, field)
      show warning banner, do NOT show partial data
```

An `AEAD.open` failure is **loud**: banner, lock the entry from display, offer "report this entry" flow. It's never silently swallowed.

### 7.6 Sharing a vault

```
ALICE (sharing) — CLIENT                                   BOB (recipient)
───────────────────────                                    ──────────────

GET /api/v1/users/<bob>/public-keys
response: {
  account_sig_pub,          # Bob's long-term identity
  account_kex_pub,          # Bob's current KEX pub
  sig_over_kex_pub          # Bob signed kex_pub with sig_priv
}

verify sig_over_kex_pub against account_sig_pub
  ← this protects against server swapping kex_pub

fingerprint_UI(alice.sig_pub, bob.sig_pub):
  compute safety_number = base32(SHA-256(alice.sig_pub || bob.sig_pub))[:60 bits]
  show to Alice; Alice verifies with Bob out-of-band (QR, voice, in-person)
  cache as "verified" in local identity store

wrapped_for_bob = HPKE.seal(
  recipient = bob.account_kex_pub,
  info      = "v1|vault-key|" + vault_uuid + "|" + bob.user_uuid,
  plaintext = vault_key
)

share_record = {
  vault_uuid, recipient_uuid: bob.user_uuid,
  wrapped_vault_key: wrapped_for_bob,
  role, expires_at,
  sharer_uuid: alice.user_uuid,
  created_at
}
share_signature = Ed25519.sign(alice.sig_priv, canonical_cbor(share_record))

POST /api/v1/passwords/vaults/<vault_uuid>/shares {
  share_record, share_signature
}

                                                           [server → Bob notification]

                                                           GET /api/v1/passwords/invitations
                                                           response: [{share_record, share_signature,
                                                                       sharer.sig_pub}]

                                                           verify share_signature against sharer.sig_pub
                                                           verify sharer.sig_pub is known / prompt fingerprint

                                                           POST /api/v1/passwords/invitations/<uuid>/accept

                                                           vault_key = HPKE.open(
                                                             recipient_priv = my.kex_priv,
                                                             ciphertext = wrapped_vault_key,
                                                             info = "v1|vault-key|"+vault_uuid+"|"+my.user_uuid
                                                           )

                                                           [vault_key now in Bob's memory,
                                                            non-extractable, wiped on lock]
```

Fingerprint verification for first-time identities is mandatory in the trust model — an unverified `sig_pub` is a TOFU pin of whatever the server served, which gives an active server a window to substitute Bob's identity. Enforcement is **contextual** (§6.6.1): hard block for cross-org / external recipients, soft warn (persistent yellow badge until verified) for recipients in the same workspace / IdP scope.

### 7.7 Revoking a member

Revocation must also rotate the vault key, because the revoked member has (or had) `vault_key` in RAM.

```
OWNER / MANAGER — CLIENT                                        SERVER
─────────────────────────                                       ──────

new_vault_key = CSPRNG(32)

For each field in each entry in the vault:
  decrypt with old vault_key → plaintext
  entry_key_new = HKDF(new_vault_key, "v1|entry-key|"+entry_uuid)
  re-encrypt with entry_key_new
  bump metadata.key_version, re-sign metadata

For each remaining member M (excluding revoked):
  wrapped_for_M = HPKE.seal(M.kex_pub, new_vault_key, info=...)

POST /api/v1/passwords/vaults/<vault_uuid>/rotate {
  expected_key_version: <current vault_key_version>,    ← optimistic CAS
  new_wrapped_keys: {member_uuid: wrapped_key, ...},
  updated_entries: [{entry_uuid, encrypted_*, metadata, metadata_sig, new_key_version}, ...],
  revoked_member_uuid
}                                     ─────────────────────────────────►
                                                                         atomic transaction,
                                                                         GUARDED by:
                                                                           if vault.key_version != expected_key_version
                                                                             return 409 Conflict
                                                                         then:
                                                                           - delete revoked VaultMember
                                                                           - replace entry ciphertexts
                                                                           - replace wrapped keys
                                                                           - bump vault.key_version
                                                                           - append audit log entry (signed)
                                      ◄────────────────────────────────
                                                                         200 (or 409 on stale version)
```

Old wrapped keys and old ciphertexts are discarded. The revoked member keeps any **copies** they made of decrypted data but gains no future access.

**Concurrency**: the `expected_key_version` gate is a compare-and-swap against the current vault state. Two managers initiating a rotation simultaneously — e.g. both revoking a member after noticing a leak — will race; the first POST wins, the second returns 409. The second client re-fetches, reconciles its intent (is the revocation already done? still another member to revoke?) and retries. Without this gate, the second POST would silently overwrite the first's re-encryption with its own, based on a now-obsolete member set — a revoked member could stay in the wrapped-key map if the second request's plan was stale.

**Cost**: client-side re-encryption is O(entries × fields) per rotation. For a 1 000-entry vault with 6 fields each, budget ~6 s of blocking client work at ~1 ms per AES-GCM op. This should run with a progress UI and be resumable (checkpoint every 100 entries) for larger vaults. The server accepts the rotation as a single atomic transaction — partial rotations are not supported (too complex to reason about which key_version any given entry is under mid-flight).

### 7.8 Vault password rotation

```
CLIENT
──────

[user enters current vault_password, new vault_password]

# 1. Re-derive the old unwrap_key. If the user mistyped their current
#    password, the AEAD decrypt below fails and we abort — no server
#    round-trip needed to "verify".
AMK_old        = Argon2id(password=current_password, salt=salt, secret=secret_key, **kdf_params)
unwrap_key_old = HKDF(AMK_old, salt=32×0x00, info="v1|unwrap", len=32)

try:
    kex_priv = AES-256-GCM.decrypt(unwrap_key_old, wrapped_kex_priv,
                                    AD="v1|account-kex-priv|" + user_uuid)
    sig_priv = AES-256-GCM.decrypt(unwrap_key_old, wrapped_sig_priv,
                                    AD="v1|account-sig-priv|" + user_uuid)
except AEADAuthenticationError:
    show "incorrect current password"; abort.

# 2. Enforce the minimum strength gate on the new password (§6.1.1).

# 3. Derive the new unwrap_key with fresh KDF params if we're upgrading.
new_params     = {m: 65536, t: 3, p: 2}         # possibly bumped
AMK_new        = Argon2id(password=new_password, salt=salt, secret=secret_key, **new_params)
unwrap_key_new = HKDF(AMK_new, salt=32×0x00, info="v1|unwrap", len=32)

# 4. Re-wrap the SAME private keys under the new unwrap_key.
wrapped_kex_priv_new = AES-256-GCM(unwrap_key_new, CSPRNG(12), kex_priv,
                                    AD="v1|account-kex-priv|" + user_uuid)
wrapped_sig_priv_new = AES-256-GCM(unwrap_key_new, CSPRNG(12), sig_priv,
                                    AD="v1|account-sig-priv|" + user_uuid)

POST /api/v1/passwords/account/rotate {
  kdf_params: new_params,
  wrapped_kex_priv: wrapped_kex_priv_new,
  wrapped_sig_priv: wrapped_sig_priv_new
}
```

**No vault re-encryption.** The vault keys haven't changed. Only the account envelope is rewritten. Cheap O(account) operation, not O(data).

---

## 8. Ciphertext format

**Authoritative specification in §6.10.** This section only records design rationale.

### Design points

- **Version byte first** — a decoder that sees an unknown version byte rejects cleanly without attempting to mis-parse a supported version.
- **Explicit algorithm IDs** — enables cohabitation of multiple AEADs during a migration window.
- **`key_version` on every ciphertext** — lets the client request the correct wrapped key when vault-key rotations are in flight.
- **Self-describing** — no external metadata table needed to parse a blob; a vault export is decodable by any v1-compliant decoder.

### Why not TLV

Fixed layout is chosen over TLV for compactness (password-vault entries are many and small; the 6-byte header + 12/24-byte IV is already 5-10% of typical plaintext size). If extension fields are needed later, a TLV area can be appended after the ciphertext with its own version byte, and that layout bumps to v2.

### Why AEAD over encrypt-then-MAC

AEAD constructions (AES-GCM, ChaCha20-Poly1305) are both well-standardized, come with formal security proofs, and have hardware acceleration on every modern device. Splitting into separate encrypt + MAC primitives doubles the key management surface (two keys per context) with no practical security benefit at the sizes we use.

---

## 9. Plaintext / encrypted / signed boundary

### Always encrypted (zero-knowledge)

- Entry name
- Entry username
- Entry password
- Entry TOTP secret
- Entry notes
- Entry custom fields
- Folder names
- Vault name, description
- Entry tags / labels
- Custom icons (if user-uploaded; app icons are by lookup)
- Attachments (file bytes)

### Plaintext (necessary for server function)

- UUIDs (relational integrity)
- Timestamps (pagination / sort)
- Folder / entry hierarchy edges (parent references)
- Vault member records (who has access, what role)
- `key_version` markers

### Plaintext by default with opt-out

- **Entry URIs** — plaintext enables domain-based autofill without decrypting every entry. UI shows a "partially protected" badge. User can toggle "encrypt URIs" per vault.
- **Entry visual icon references** — plaintext, purely cosmetic.

### Integrity-only (plaintext but signed)

- Entry metadata (timestamps, folder_uuid, URIs, icon_ref, key_version, entry_version)
- Share records
- Role assignments
- Vault member lists

**Every plaintext structure that could be maliciously rewritten is signed by its author.** A server that rewinds `updated_at`, changes a URI, flips a role, or removes a member silently fails signature verification.

### UI contract

The UI must make this boundary **visible** to the user. A vault with encrypted URIs shows a solid green badge. A vault with plaintext URIs shows a yellow "partially protected" badge with a tooltip explaining the trade-off. Users should never be surprised.

---

## 10. Algorithm agility

Every crypto-sensitive record includes an algorithm identifier or version. Migration is lazy:

| Record | Agility field | Upgrade path |
|--------|---------------|--------------|
| Account envelope | `kdf_algo`, `kdf_params` | Rewritten at next password rotation, or on forced upgrade |
| Account keypair | Implied by `account_kex_pub`'s `alg_id` prefix | Full identity rotation (rare) |
| Vault key | `key_version` | Rotated on demand or on member revocation |
| Entry ciphertext | `version`, `aead_id`, `kdf_id` in header | Rewritten at next entry write, or via bulk re-key job |
| Share wrapped key | HPKE built-in `kem_id`, `kdf_id`, `aead_id` | Re-shared with new HPKE suite |
| Signature | `sig_alg_id` prefix on all signatures | Forward-compatible by concatenation (see PQ) |

### Upgrade policy

- **Minor** (e.g. AES-GCM → XChaCha20-Poly1305): lazy, triggered by write.
- **Major** (e.g. vault-key format change): proactive, via "re-key this vault" admin action.
- **Identity** (e.g. Ed25519 → Ed25519+ML-DSA hybrid): opt-in per user, then enforced.

### Deprecation

A deprecated wire format stays **readable for 12 months** after the next major version (`vN+1`) reaches general availability. During that window, writes immediately use `vN+1` (lazy migration on next entry write — §10 upgrade policy), but reads of `vN` ciphertexts continue to work. After 12 months, reads of `vN` are refused; users with un-migrated entries see a "your vault uses an unsupported format — re-enter your password to migrate" prompt that forces a full re-encrypt under `vN+1` before any further access. The 12-month window covers users who only log in once a year (annual subscription renewals, dormant work accounts).

The clock starts at the public release tag of `vN+1`, recorded as `formats.<n>.deprecated_at` in a server-side migration registry — this prevents the deprecation date from being implicit in deployment time and lets us extend it once if telemetry shows a long tail of un-migrated vaults.

---

## 11. Post-quantum roadmap

Current NIST PQC standardization status (2026):
- **ML-KEM** (FIPS 203): final. Replaces X25519 for key encapsulation.
- **ML-DSA** (FIPS 204): final. Replaces Ed25519 for signatures.
- **SLH-DSA** (FIPS 205): final, hash-based, larger signatures — backup if ML-DSA breaks.

### Strategy

**Hybrid today, PQ-only tomorrow.**

| Use | Today | Phase 2 (PQ hybrid) | Phase 3 (PQ only) |
|-----|-------|---------------------|-------------------|
| Vault key wrapping | X25519 HPKE | X25519 + ML-KEM-768 hybrid (concat ciphertexts) | ML-KEM-1024 |
| Signatures | Ed25519 | Ed25519 + ML-DSA-44 (concat signatures) | ML-DSA-65 |
| Symmetric AEAD | AES-256-GCM | AES-256-GCM (already PQ-resistant) | Unchanged |
| KDF | Argon2id, HKDF-SHA256 | Unchanged (PQ-resistant at 256-bit output) | Unchanged |

In hybrid mode, **both** primitives must be broken to compromise a record. Wire cost: ~1 KB extra per shared vault key (ML-KEM-768 ciphertext is ~1 KB). Signature cost: ~2.5 KB extra (ML-DSA-44 sig is ~2.4 KB + 32 B Ed25519). Acceptable for a password vault.

The `kem_id`, `kdf_id`, `aead_id`, `sig_alg_id` enums already have reserved slots for these — no re-architecture needed.

---

## 12. Client-side defense in depth

Browser crypto is fundamentally vulnerable to "the server serves malicious JavaScript". Three lines of defense:

1. **Strict Content-Security-Policy**:
   ```
   default-src 'none';
   script-src 'self';
   connect-src 'self' https://<known-api-origin>;
   style-src 'self';
   img-src 'self' data: blob:;
   object-src 'none';
   base-uri 'none';
   form-action 'self';
   frame-ancestors 'none';
   report-to csp-endpoint;
   ```
   No `'unsafe-inline'`, no `'unsafe-eval'`. A `Report-To` / `report-to` directive MUST point at an internal endpoint that logs violations — without it, a silent tampering attempt (e.g. injected inline script blocked by CSP) leaves no server-side trace. Reports go to a distinct log stream, not to Sentry (violation payloads can contain snippets of user-visible DOM).

   **Implementation**: use **`django-csp`** for both the policy header and the report endpoint. Retention 14 days in a dedicated log stream (separate DB connection from vault blobs). Alert when a single origin produces >10 violations/min — that signature is consistent with an active injection attempt and should page on-call.

2. **Subresource Integrity** on `vault-crypto.js`, `vault-browser.js`, and the HPKE/Argon2 WASM bundles. Hash pinned in the HTML, reviewed per release.

3. **Service Worker integrity check** (ProtonMail-style): on each load, the SW fetches a signed `app-manifest.json` listing expected hashes for every asset. Any mismatch → SW refuses to activate, shows a "app integrity verification failed" screen. Manifest signature key is a long-term key rotated separately from the deployment key.

### Memory hygiene

The pattern for unwrapping any private-key material from an AES-GCM-wrapped blob (account `kex_priv`, account `sig_priv`) or from an HPKE-sealed blob (`vault_key`) is:

```js
// 1. Decrypt to a transient Uint8Array.
const rawBytes = await crypto.subtle.decrypt(
  { name: 'AES-GCM', iv, additionalData },
  unwrapKey,
  wrappedBlob
);

// 2. Immediately import as a non-extractable CryptoKey.
const cryptoKey = await crypto.subtle.importKey(
  'raw',                            // or 'pkcs8' for kex_priv/sig_priv depending on storage
  rawBytes,
  algorithm,
  /*extractable*/ false,
  usages
);

// 3. Zero the transient buffer immediately. The GC may not reclaim it
//    instantly, but cooperate with the runtime.
new Uint8Array(rawBytes).fill(0);
```

This pattern (raw decrypt + `importKey({extractable: false})` + wipe) is mandated over native `crypto.subtle.unwrapKey` for two reasons. First, HPKE has no WebCrypto-native equivalent — `@hpke/core.open()` returns raw bytes regardless, so the `vault_key` path forces the raw-import step anyway. Picking the same pattern for the AES-GCM-wrapped account keys keeps both code paths uniform. Second, pinning a `unwrapKey` mandate adds a hard dependency on recent WebCrypto features (X25519/Ed25519 support in `unwrapKey` is new) without a corresponding security uplift over the wipe-immediately pattern.

The honest cost: a ~10 ms window during unlock where the raw private-key bytes exist in a JS-accessible buffer. Same threat-model exposure as during normal cryptographic use; same mitigation (avoid running the vault on a compromised device).

Other rules:
- `extractable: false` on every `CryptoKey` produced from the unwrap step. The biometric-keystore path (§5.1) is the only place that uses `extractable: true` — and only on `unwrap_key` itself, never on `kex_priv` / `sig_priv` / `vault_key` / `entry_key`.
- `stretchedKey`, `AMK`, raw password: wipe from memory as soon as they're used. `password = ''; AMK.fill(0)`. Browser may not zero the underlying buffer (GC), but cooperate with it.
- Auto-lock: configurable timer (default 5 min idle), lock on `visibilitychange` hidden, lock on page unload.
- Clipboard: one active timer per copy; verify clipboard content still matches before clearing; cancellable via UI.

### Input hygiene

- Master password input: `autocomplete="current-password"` (to leverage browser password manager prevention, counterintuitive but correct — `"new-password"` on setup).
- Entry password inputs for editing vault content: `autocomplete="off"` + `spellcheck="false"`.
- No `<input>` value rendered outside a controlled form scope.

---

## 13. Server responsibilities

The server is deliberately **dumb**:

- Store ciphertexts and signed metadata. Never inspect their contents.
- Authenticate sessions via the existing Django auth (standard `ModelBackend`, unchanged). The vault password is **not** validated server-side.
- Enforce **authorization** on every request: role checks via `SharingService.can_write` / `can_manage`.
- **Rate-limit** password-touching endpoints:
  - Login init: 10/min/IP, 30/hour/account.
  - Login finalize: 10/min/IP.
  - Vault password rotation: 5/hour/account.
  - Unlock (for legacy vaults if any): 10/min/vault.
  - `GET /account/envelope`: **60/hour/user with a 10/min burst, plus 200/hour/IP** as a horizontal-fan-out catch. The user limit accommodates worst-case multi-tab + 5-min idle-lock (~12 unlocks/hour/tab × 5 tabs = 60). The per-IP limit detects exfiltration that distributes across compromised cookies. Both limits return `429` with `Retry-After`; the client throttles silently for short windows and prompts the user only after sustained 429s. Re-measure once we have telemetry — these numbers are the v1 starting point, not gospel.
- **Audit log**: every auth, every share op, every rotation, every destructive action. **Hash-chained** (each entry includes `SHA-256(previous_entry_bytes)`) so re-ordering or rewriting is detectable by anyone who saved a later hash — a tamper-evident log, not a MAC-authenticated one (an HMAC would require a key, and any key the server holds it can also rewrite under). Optionally, the client periodically signs the current chain head with its `sig_priv` and submits the signature: that gives the user an independent anchor to detect server-side rewrites on next login. Logs live in a separate DB from vault blobs to compartmentalize breach.
- **Log scrubbing middleware**: strip `password`, `secret_key`, `session_key`, and any field matching `wrapped_*` / `encrypted_*` / `sig_*` from all request/response logs, Sentry, APM. (There is no `client_master_hash` or similar server-side password proof in this design — see §6.7.)
- **Backups**: encrypted at rest with a server-managed key; see "key escrow" caveat in §14.

### What the server must NOT do

- Inspect the content of any `encrypted_*` / `wrapped_*` / `ciphertext` field.
- Derive or compute any key material. All crypto is client-side.
- Log any body from the login, unlock, rotate-password, or setup-vault endpoints.
- Serve JavaScript from a CDN it doesn't control (SRI only works if hashes are pinned).

---

## 14. Open questions

### Flows not yet specified

#### Must ship in v1 (non-negotiable)

- **Account deletion.** Required for GDPR Art. 17 compliance — non-negotiable. When a Django user is deleted: purge all their vault blobs, their `AccountIdentity` row, their `sig_pub`/`kex_pub` publications, and rewrite every `share_record` where they were sharer or recipient. Shared vaults where the deleted user was a member need their wrap removed; remaining members' wraps are unchanged (no re-encryption). Shared vaults where the deleted user was the **sole** member are hard-deleted (no grace period — the deletion is user-initiated, not admin-imposed). Signed `share_record`s referencing the deleted `sig_pub` remain cryptographically verifiable for historical entries but the UI marks the sharer as "deleted". Detailed design needed before prod.

- **Forgot-password user-facing flow.** Structurally there is none: the vault password cannot be reset without the current password, and the emergency kit holds `secret_key`, not the password. UI must communicate this **before** the user sets the password — no "forgot?" link that suggests otherwise. Onboarding must force a non-skippable "save your emergency kit" step before the first vault becomes unlockable. Copy: "Your vault password is the only thing that decrypts your data. Nobody on our side can read it or reset it. If you lose it, your data is gone — even if you still have your emergency kit."

- **Export.** The escape hatch that makes "we can lose your data" acceptable. v1 ships at minimum: Bitwarden JSON (de-facto interchange standard) + a project-native encrypted bundle (binary, contains the vault's ciphertexts + the recipient-keyed wrap so it can be re-imported into a fresh account). Without an export path, the project's zero-knowledge claim becomes hostile — users have no way to leave with their data.

#### Acceptable as v1.1

- **Device revocation / "panic rotate".** A device that has unlocked the vault has had `kex_priv`, `sig_priv`, and one-or-more `vault_key`s in its RAM. Revoking the Django session kills future server round-trips but does NOT invalidate what the device cached. There is no crypto-level "revoke this device" primitive — the practical answer is a "panic rotate" button: re-wrap the account privates under a fresh password (re-deriving everything) and rotate every `vault_key` the user holds (heavy: O(all entries across all vaults)). Document the limitation in §3 "Explicit non-promises". Implementing the button is a v1.1 follow-up — without it, users still have a manual recourse (rotate password + rotate each vault key one by one).

- **Sig-keypair rotation UX.** The crypto is specified in §6.6.1 (signed rotation attestation under old `sig_priv`). The UX flow — when the client offers rotation, what to show when peers' clients next fetch, the "I lost old `sig_priv`" recovery path that forces out-of-band re-verification by every peer — can wait until we have a real rotation case.

- **Import** (the inverse of v1's export). Easier than export to delay: people leave password managers more often than they arrive at one. v1.1 adds Bitwarden JSON import + 1Password 1pux. Validate structure, warn on data loss for tags / custom fields that don't map cleanly.

### Genuinely open design questions

- **Emergency access / legacy**: do we support a "trusted contact can read my vault after I die"? If yes, each vault also wraps its key for a designated emergency recipient (HPKE.seal under their `kex_pub`) with a server-enforced delay + notification. The recipient needs their own account / identity to have a `kex_pub` at all — so this only works within the system's user base, not for arbitrary email addresses. Adds complexity; can be v2.
- **Server operator recovery**: should there be *any* backup for "I lost password AND secret key"? Current answer: no (pure zero-knowledge). Alternative: opt-in "server-assisted recovery" where a Shamir share of `unwrap_key` is held by the server and unlocked via email + delay. Reduces security; explicit opt-in only.
- **Multi-device sync without retyping the secret_key**: after first login on a new device, secret_key can be transferred from an existing device via an in-product encrypted channel (scan a QR code that contains an HPKE-sealed secret_key, scoped to a one-time device key). Needs design.
- **Audit log verification UI**: users should be able to verify the hash chain of their own audit log to detect server tampering (compare chain-head to the last-seen head, optionally cross-check client-signed chain-head anchors). Bandwidth-friendly; straightforward to implement.
- **Search**: client-side only for now. Performance concern: encrypted tags/labels mean any filter requires decrypting every entry's tags locally (~30k AES-GCM ops for a 10k-entry vault × 3 tags, ~3 s blocking). Server-side search via "encrypted indexes" (PRF on tokens with per-vault keys) is a known follow-up with known trade-offs (co-occurrence leakage).
- **File attachments in entries**: encrypted with entry-key using chunked AES-GCM (each chunk has its own IV + AD with chunk index). TBD design.
- **XChaCha20-Poly1305 vs AES-256-GCM as default**: XChaCha has a nicer 24-byte nonce (no collision worry) and simpler constant-time impl in WASM, but costs ~100 KB of WASM payload. Decision deferred to when the first migration lands.
- ~~Deprecation window for old formats~~ (resolved — see §10).

---

## 15. Migration path from current implementation

The current PR #103 implements:
- Per-vault master passwords (instead of a single user-wide vault password).
- PBKDF2-SHA256 client-side KDF (no Argon2id).
- AES-GCM without `additionalData`.
- No ciphertext version byte.
- Sends a client-derived hash of the password to the server for verification (v1-design keeps this out entirely — no server-side password verification needed).
- No HPKE (envisaged per-user ECDH keypair but no implemented sharing flow).
- No public-key fingerprint verification.
- No signed metadata.
- `notes` stored in cleartext.

Direct rewrite is safer than incremental change because the key hierarchy is fundamentally different (one account identity, not one per vault). Proposed sequence:

1. **Design review**: circulate this doc, align on approach, open RFC for deviations.
2. **Foundation PR**: introduce `AccountIdentity` model (kex + sig keypairs), Argon2id client, HPKE client, signed metadata plumbing. No user-visible changes yet.
3. **Flow PR**: new onboarding flow (set vault password + show emergency kit), vault-unlock dialog with local AEAD verification, vault password rotation, fingerprint UI. Old flow still works for existing accounts until step 4 migrates them.
4. **Vault migration PR**: on first login after this PR, migrate the user's single existing vault by re-encrypting under the new key hierarchy. New vaults go through the new flow by default.
5. **Sharing PR**: HPKE-based share flow, signed share records, fingerprint verification UI.
6. **Cleanup PR**: remove old code paths, retire per-vault master password, delete unused `UserKeyPair` model fields.

Existing data does not break during this rollout because old ciphertexts are preserved until the per-user migration point in step 4.

**Coordination is data-driven, not flag-driven.** The dispatch between "old vault flow" and "new vault flow" is a single check on every request: does this user have an `AccountIdentity` row? If yes, route to the new flow; if no, route to the legacy code path. Step 4 (the migration PR) creates the `AccountIdentity` row as the final step of a successful migration — a user is "on the new flow" exactly when their data is. No `PASSWORDS_V2` boolean flag, no environment variable, no feature-flag service. This aligns with CLAUDE.md's "don't use feature flags when you can just change the code": the coexistence is justified by the data being in two states, not by a deployment switch.

Step 6's cleanup PR can land safely once telemetry shows zero users without an `AccountIdentity` row for N consecutive days (N ≈ 60 to cover dormant accounts plus the deprecation window from §10). At that point the legacy code path is dead code and can be removed in a single commit.

---

## 16. References

### Specifications
- [RFC 5869](https://www.rfc-editor.org/rfc/rfc5869) — HKDF
- [RFC 8439](https://www.rfc-editor.org/rfc/rfc8439) — ChaCha20 and Poly1305
- [RFC 8949](https://www.rfc-editor.org/rfc/rfc8949) — CBOR
- [RFC 9106](https://www.rfc-editor.org/rfc/rfc9106) — Argon2
- [RFC 9180](https://www.rfc-editor.org/rfc/rfc9180) — HPKE
- [FIPS 203](https://csrc.nist.gov/pubs/fips/203/final) — ML-KEM
- [FIPS 204](https://csrc.nist.gov/pubs/fips/204/final) — ML-DSA

### Prior art
- [Bitwarden Security Whitepaper](https://bitwarden.com/help/bitwarden-security-white-paper/)
- [1Password Security Design](https://1passwordstatic.com/files/security/1password-white-paper.pdf)
- [Signal Safety Numbers](https://signal.org/blog/safety-number-updates/)
- [ProtonMail Bundle Integrity](https://proton.me/blog/protonmail-source-code)

### Libraries (candidate choices)
- `@hpke/core`, `@hpke/dhkem-x25519`, `@hpke/chacha20poly1305` — HPKE in JS/TS
- `argon2-browser` — Argon2id WASM
- `@noble/ed25519`, `@noble/curves` — audited pure-JS elliptic curves (fallback if WebCrypto's Ed25519 isn't available on a target)
- `cbor-x` — deterministic CBOR encoding
- `argon2-cffi`, `nacl`, `pyhpke` — server-side parity (only used for verification, not decryption)
