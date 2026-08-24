"""The vendored bundle against the real API, in a real browser.

Everything else proves this flow by transitivity. The Django tests build an
attestation with the Python reference and hand it to the server; the frozen
vectors prove the bundle and that reference agree. The pair a user actually
runs - browser to server - is covered by nobody, and a chain of three
implementations is exactly where a mismatch hides.

It also covers what no unit test reaches: WebCrypto producing the key material
rather than a fixture, and the session and CSRF path the API sits behind.
"""

from django.contrib.auth import get_user_model

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.vault.models import AccountIdentity

User = get_user_model()

BUNDLE_URL = "/static/vault/ui/js/vendor/vault-crypto.js"

# Onboarding as the browser will run it: ask the server for the account UUID
# and the salt, mint the two key pairs, attest the key exchange public key with
# the signature key, and submit. The bundle owns every byte the server checks -
# the algorithm labels, the catalogue string and the signature prefix.
ONBOARD = """
async () => {
  const V = window.VaultCrypto;
  const csrf = document.cookie.match(/csrftoken=([^;]+)/)[1];
  const post = (url, body) => fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
    body: JSON.stringify(body || {}),
  });

  const started = await post('/api/v1/vault/account/init');
  const { account_uuid: accountUuid, kdf_salt: kdfSalt } = await started.json();

  const kexPair = await crypto.subtle.generateKey('X25519', true, ['deriveBits']);
  const sigPair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);

  const rawPublic = async (key) =>
    new Uint8Array(await crypto.subtle.exportKey('raw', key));
  // WebCrypto exports an Ed25519 private key as PKCS#8 only, and the bundle
  // signs from the bare seed - the last 32 bytes of that fixed structure.
  const pkcs8 = new Uint8Array(await crypto.subtle.exportKey('pkcs8', sigPair.privateKey));
  const seed = pkcs8.slice(-32);

  const kexPublic = V.toBase64Url(
    V.encodePublicKey(await rawPublic(kexPair.publicKey), V.PUBKEY_ALG_X25519)
  );
  const sigPublic = V.toBase64Url(
    V.encodePublicKey(await rawPublic(sigPair.publicKey), V.PUBKEY_ALG_ED25519)
  );
  const attestation = V.toBase64Url(
    await V.signBytes(seed, V.AD.kexPubPayload(accountUuid, kexPublic))
  );

  const body = {
    kdf_algo: 'argon2id',
    kdf_params: { m: 65536, t: 3, p: 2 },
    kex_public: kexPublic,
    sig_public: sigPublic,
    // Not real ciphertexts: sealing them needs the vault password, which
    // belongs to the onboarding screen. The server stores them opaquely and
    // checks only that they are base64url, which is what is exercised here.
    wrapped_kex_priv: V.toBase64Url(V.randomBytes(64)),
    wrapped_sig_priv: V.toBase64Url(V.randomBytes(64)),
    sig_over_kex_pub: attestation,
  };

  const finalized = await post('/api/v1/vault/account/finalize', body);
  const envelope = await fetch('/api/v1/vault/account/envelope');

  const tampered = { ...body, sig_over_kex_pub: V.toBase64Url(V.randomBytes(65)) };

  return {
    initStatus: started.status,
    accountUuid,
    kdfSalt,
    finalizeStatus: finalized.status,
    envelopeStatus: envelope.status,
    envelope: await envelope.json(),
    submitted: body,
    replayStatus: (await post('/api/v1/vault/account/finalize', tampered)).status,
  };
}
"""


# Django's test client exempts itself from CSRF, so no other test in the
# project can tell whether these endpoints are protected at all.
POST_WITHOUT_CSRF = """
async () => {
  const response = await fetch('/api/v1/vault/account/init', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  return response.status;
}
"""


class AccountEnvelopeInteropTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="owner")
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/vault")
        self.page.add_script_tag(url=BUNDLE_URL)
        self.result = self.page.evaluate(ONBOARD)

    def test_the_server_accepts_an_identity_the_browser_built(self):
        self.assertEqual(self.result["initStatus"], 201)
        self.assertEqual(
            self.result["finalizeStatus"],
            201,
            f"finalize refused the browser's attestation: {self.result}",
        )

        identity = AccountIdentity.objects.get(user=self.user)
        self.assertEqual(identity.state, AccountIdentity.State.ACTIVE)
        self.assertEqual(identity.kex_public, self.result["submitted"]["kex_public"])
        self.assertEqual(str(identity.uuid), self.result["accountUuid"])
        self.assertEqual(identity.kdf_salt, self.result["kdfSalt"])

    def test_the_envelope_returns_what_the_browser_submitted(self):
        self.assertEqual(self.result["envelopeStatus"], 200)
        envelope = self.result["envelope"]
        for field in (
            "kex_public",
            "sig_public",
            "wrapped_kex_priv",
            "sig_over_kex_pub",
        ):
            with self.subTest(field=field):
                self.assertEqual(envelope[field], self.result["submitted"][field])
        self.assertEqual(envelope["state"], "active")

    def test_a_second_finalize_is_refused(self):
        """The identity is active by now, so the replay is refused before its
        attestation is even looked at - and the stored keys are the first
        submission's."""
        self.assertEqual(self.result["replayStatus"], 409)
        identity = AccountIdentity.objects.get(user=self.user)
        self.assertEqual(
            identity.sig_over_kex_pub, self.result["submitted"]["sig_over_kex_pub"]
        )


class CsrfTests(PlaywrightTestCase):
    def test_a_post_without_the_csrf_token_is_refused(self):
        user = self.create_user(username="owner")
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/vault")

        self.assertEqual(self.page.evaluate(POST_WITHOUT_CSRF), 403)
        self.assertFalse(AccountIdentity.objects.exists())
