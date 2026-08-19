"""The vendored crypto bundle, replayed in a real browser.

Every other test of this bundle runs it under Node, in the project's `node:vm`
loader. That proves the code is self-consistent; it does not prove it works
where it actually ships. WebCrypto is a browser API with per-engine coverage -
Ed25519 in particular arrived late and unevenly - and `crypto.subtle` does not
exist at all outside a secure context. Both are assumptions the Node suite
cannot test, and both would surface as a user who simply cannot open a vault.

Chromium, Firefox and WebKit are all exercised. An engine whose build lacks
WebCrypto's secure curves skips with a message naming the missing primitive
rather than reporting a defect in the bundle.
"""

import json
import pathlib
import unittest

from django.test import override_settings

from workspace.common.tests.e2e.base import PlaywrightTestCase

VECTORS = json.loads(
    (pathlib.Path(__file__).resolve().parent.parent / "crypto_vectors.json").read_text(
        encoding="utf-8"
    )
)

CORPUS = json.loads(
    (pathlib.Path(__file__).resolve().parent.parent / "fuzz_corpus.json").read_text(
        encoding="utf-8"
    )
)

BUNDLE_URL = "/static/vault/ui/js/vendor/vault-crypto.js"

# Replays every frozen vector through the bundle and returns the ids that did
# not reproduce. Kept as one round-trip: Argon2id alone costs a few hundred
# milliseconds per vector, and a chatty test would pay that in latency too.
REPLAY = """
async (vectors) => {
  const V = window.VaultCrypto;
  if (!V) return ['bundle did not publish window.VaultCrypto'];
  const failures = [];
  const b64 = (bytes) => V.toBase64Url(bytes);
  const utf8 = (text) => new TextEncoder().encode(text);

  for (const v of vectors.argon2id) {
    const amk = await V.deriveAmk({
      password: v.password,
      secretKey: V.fromBase64Url(v.secret_key_b64),
      salt: V.fromBase64Url(v.salt_b64),
      params: v.params,
    });
    if (b64(amk) !== v.expected_amk_b64) failures.push('argon2id/' + v.id);
  }

  for (const v of vectors.hkdf) {
    const out = await V.hkdf(V.fromBase64Url(v.ikm_b64), utf8(v.info), 32);
    if (b64(out) !== v.expected_b64) failures.push('hkdf/' + v.id);
  }

  for (const v of vectors.aead) {
    const ad = utf8(v.ad);
    const raw = await V.seal(V.fromBase64Url(v.key_b64), utf8(v.plaintext), ad, {
      iv: V.fromBase64Url(v.iv_b64),
      keyVersion: v.key_version,
      kdfId: v.kdf_id,
    });
    if (b64(raw) !== v.expected_wire_b64) failures.push('aead/' + v.id);
    const plain = await V.open(V.fromBase64Url(v.key_b64), raw, ad);
    if (new TextDecoder().decode(plain) !== v.plaintext) {
      failures.push('aead-reopen/' + v.id);
    }
  }

  // HPKE draws a fresh ephemeral key per seal, so the assertion is that the
  // browser opens what the reference sealed - the direction that matters for
  // reading data written by another implementation.
  for (const v of vectors.hpke) {
    const opened = await V.hpkeOpen(
      V.fromBase64Url(v.recipient_sk_b64),
      utf8(v.info),
      V.fromBase64Url(v.expected_sealed_b64)
    );
    if (b64(opened) !== v.plaintext_b64) failures.push('hpke/' + v.id);
  }

  for (const v of vectors.cbor) {
    if (b64(V.canonicalCbor(v.payload)) !== v.expected_b64) {
      failures.push('cbor/' + v.id);
    }
  }

  // Signatures are verified, not compared. Safari signs with added noise
  // rather than deterministically, so its bytes legitimately differ from the
  // reference for the same key and message - while still verifying. Byte
  // equality is asserted on the Node side, where the engine is deterministic.
  for (const v of vectors.ed25519) {
    const pk = V.fromBase64Url(v.pk_b64);
    try {
      if (v.message_b64) {
        const message = V.fromBase64Url(v.message_b64);
        await V.verifyBytes(pk, message, await V.signBytes(V.fromBase64Url(v.sk_b64), message));
        // The reference's own signature must verify here too: that is the
        // interoperability that actually matters for reading stored data.
        await V.verifyBytes(pk, message, V.fromBase64Url(v.expected_sig_b64));
      } else {
        const payloadBytes = V.canonicalCbor(v.payload);
        const signature = await V.sign(V.fromBase64Url(v.sk_b64), v.payload);
        await V.verify(pk, payloadBytes, signature, v.payload.type);
        await V.verify(pk, payloadBytes, V.fromBase64Url(v.expected_sig_b64), v.payload.type);
      }
    } catch (e) {
      failures.push('ed25519/' + v.id + ': ' + e.message);
    }
  }

  return failures;
}
"""


# The Node suite runs the bundle through a `node:vm` context, which is a second
# realm; a bundled library that branches on `constructor === Array` behaves
# differently there than on a page. Replaying the corpus here is what makes it
# an assertion about production rather than about the harness.
REPLAY_CORPUS = """
async (corpus) => {
  const V = window.VaultCrypto;
  const failures = [];
  const b64 = (bytes) => V.toBase64Url(bytes);

  for (const item of corpus.cbor) {
    if (b64(V.canonicalCbor(item.payload)) !== item.expected_b64) failures.push(item.id);
  }

  for (const item of corpus.aead) {
    const key = V.fromBase64Url(item.key_b64);
    const ad = V.fromBase64Url(item.ad_b64);
    const raw = await V.seal(key, V.fromBase64Url(item.plaintext_b64), ad, {
      iv: V.fromBase64Url(item.iv_b64),
      keyVersion: item.key_version,
      kdfId: item.kdf_id,
    });
    if (b64(raw) !== item.expected_wire_b64) failures.push(item.id);
  }

  return failures;
}
"""


class EngineChecks:
    """What has to hold on every engine, not just the one we develop on.

    A plain mixin rather than a base test case, so the runner collects only the
    concrete classes below.
    """

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        # Probed before anything is allocated. The base class starts the live
        # server and the Playwright driver before it launches the browser, and
        # unittest does not run tearDownClass after a setUpClass that raised -
        # deciding to skip in there would leak a server thread and a driver
        # process for the rest of the suite. Probing first also keeps a real
        # failure, a port clash or a database error, from being relabelled as a
        # missing browser.
        try:
            with sync_playwright() as probe:
                executable = pathlib.Path(
                    getattr(probe, cls.BROWSER_NAME).executable_path
                )
            installed = executable.exists()
        except Exception:  # pragma: no cover - depends on the machine
            installed = False
        if not installed:
            raise unittest.SkipTest(f"{cls.BROWSER_NAME} is not installed")
        super().setUpClass()

    def _load_bundle(self):
        self.page.goto(f"{self.live_server_url}/login")
        self.page.add_script_tag(url=BUNDLE_URL)
        self._require_secure_curves()

    def _require_secure_curves(self):
        """Skip, loudly, on a build without Ed25519 and X25519.

        Playwright's WebKit does not carry them on every platform, while the
        Safari that ships to users has since version 17. Skipping keeps a test
        engine's gap from being reported as a defect in the bundle - and the
        message says which primitive is missing, so a genuine regression on a
        target engine is not mistaken for this.
        """
        missing = self.page.evaluate("""
            async () => {
              try {
                await crypto.subtle.importKey(
                  'raw', new Uint8Array(32), 'Ed25519', false, ['verify']);
                return null;
              } catch (e) { return e.name; }
            }
        """)
        if missing:
            self.skipTest(
                f"{self.BROWSER_NAME} build without WebCrypto secure curves "
                f"({missing}); shipping Safari has had them since 17"
            )

    def test_every_frozen_vector_replays_in_the_browser(self):
        self._load_bundle()
        failures = self.page.evaluate(REPLAY, VECTORS)
        self.assertEqual(failures, [], f"vectors that did not reproduce: {failures}")

    def test_webcrypto_offers_the_algorithms_the_bundle_needs(self):
        """Probes the operations, not just the key imports.

        An engine can accept `importKey` for an algorithm and still refuse to
        sign with it, so importing alone proves nothing. Each probe runs the
        call the bundle actually makes and reports per algorithm, so a failure
        names the primitive rather than the engine.
        """
        self._load_bundle()
        available = self.page.evaluate("""
            async () => {
              const out = {};
              const probe = async (name, fn) => {
                try { await fn(); out[name] = true; }
                catch (e) { out[name] = `${e.name}: ${e.message}`; }
              };
              const seed = new Uint8Array(32);
              // The fixed PKCS#8 prelude the bundle prepends to a raw seed.
              const pkcs8 = new Uint8Array([
                0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b,
                0x65, 0x70, 0x04, 0x22, 0x04, 0x20, ...seed,
              ]);

              await probe('hkdf-derive', async () => {
                const k = await crypto.subtle.importKey(
                  'raw', new Uint8Array(32), 'HKDF', false, ['deriveBits']);
                await crypto.subtle.deriveBits(
                  { name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(32),
                    info: new Uint8Array(0) }, k, 256);
              });

              await probe('aes-gcm-encrypt', async () => {
                const k = await crypto.subtle.importKey(
                  'raw', new Uint8Array(32), 'AES-GCM', false, ['encrypt']);
                await crypto.subtle.encrypt(
                  { name: 'AES-GCM', iv: new Uint8Array(12), tagLength: 128 },
                  k, new Uint8Array(4));
              });

              await probe('ed25519-import-private', () => crypto.subtle.importKey(
                'pkcs8', pkcs8, 'Ed25519', false, ['sign']));

              await probe('ed25519-sign', async () => {
                const k = await crypto.subtle.importKey(
                  'pkcs8', pkcs8, 'Ed25519', false, ['sign']);
                await crypto.subtle.sign('Ed25519', k, new Uint8Array(4));
              });

              await probe('ed25519-import-public', () => crypto.subtle.importKey(
                'raw', new Uint8Array(32), 'Ed25519', false, ['verify']));

              return out;
            }
        """)
        unsupported = {k: v for k, v in available.items() if v is not True}
        self.assertEqual(unsupported, {}, f"WebCrypto is missing: {unsupported}")


class CryptoBundleBrowserTests(EngineChecks, PlaywrightTestCase):
    """Chromium, plus everything too slow or too engine-specific to triplicate."""

    def test_the_generated_corpus_replays_in_the_browser(self):
        self._load_bundle()
        failures = self.page.evaluate(REPLAY_CORPUS, CORPUS)
        self.assertEqual(
            failures,
            [],
            f"{len(failures)} generated cases diverged (seed {CORPUS['seed']})",
        )

    def test_the_unlock_derivation_stays_within_its_time_budget(self):
        """The key derivation is the whole cost of an unlock, and its parameters
        are a security decision: too cheap and it is brute-forceable, too dear
        and users are pushed to lower them. The ceiling here is deliberately
        generous - it is meant to catch a parameter that moved by an order of
        magnitude, not to benchmark a runner.
        """
        self._load_bundle()
        elapsed = self.page.evaluate("""
            async () => {
              const V = window.VaultCrypto;
              const started = performance.now();
              await V.deriveAmk({
                password: 'Tr0ub4dor&3',
                secretKey: V.randomBytes(32),
                salt: V.randomBytes(32),
              });
              return performance.now() - started;
            }
        """)
        print(f"[e2e] derivation de la cle maitresse : {elapsed:.0f} ms")
        self.assertLess(
            elapsed, 5000, f"key derivation took {elapsed:.0f} ms, budget is 5000 ms"
        )

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_the_vault_cannot_work_outside_a_secure_context(self):
        """Pins the failure mode rather than a capability.

        `crypto.subtle` is undefined on a plain-HTTP origin, so every primitive
        this module owns is unavailable there: the vault is inoperable on an
        instance served without TLS. That constraint is invisible in the code
        it constrains, which is what this test is for.

        The browser only treats localhost and loopback literals as trustworthy,
        so an ordinary hostname mapped onto the live server produces a genuinely
        insecure origin.
        """
        port = self.live_server_url.rsplit(":", 1)[1]
        browser = self._playwright.chromium.launch(
            headless=self.HEADLESS,
            args=["--host-resolver-rules=MAP vault-insecure.test 127.0.0.1"],
        )
        try:
            page = browser.new_context().new_page()
            page.goto(f"http://vault-insecure.test:{port}/login")
            state = page.evaluate(
                "() => ({ secure: window.isSecureContext, subtle: typeof crypto.subtle })"
            )
        finally:
            browser.close()

        self.assertFalse(state["secure"], "expected a non-secure origin")
        self.assertEqual(
            state["subtle"],
            "undefined",
            "crypto.subtle exists here, so the premise of this test is wrong",
        )


class CryptoBundleFirefoxTests(EngineChecks, PlaywrightTestCase):
    BROWSER_NAME = "firefox"


class CryptoBundleWebKitTests(EngineChecks, PlaywrightTestCase):
    BROWSER_NAME = "webkit"
