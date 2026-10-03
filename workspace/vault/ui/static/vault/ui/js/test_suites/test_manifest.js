// Test-only: switches the manifest states of the test bundle before anything
// reads the current suite. Loaded only when the server runs the rehearsal of
// an algorithm replacement; the production bundle has no such door.
(function () {
  const source = document.getElementById('vault-test-manifest');
  if (!source) return;
  window.vaultCrypto.installTestManifest(JSON.parse(source.textContent));
})();
