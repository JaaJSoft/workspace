// The emergency kit: the sheet a recovery key is saved on, wherever one is
// handed over - onboarding, and the dialog that replaces a key.
//
// The PDF is built in the page by the on-demand bundle, so the key never
// reaches the server; see test_secret_never_posted.py.
window.vaultEmergencyKit = (function () {
  let bundlePending = null;

  // The PDF generator outweighs everything else the vault page loads, and one
  // dialog needs it: a single script tag, the first time a kit is asked for.
  // A page that already carries the bundle - onboarding does - resolves at
  // once. A failed load is not remembered, so the next attempt tries again.
  function load(bundleUrl) {
    if (window.vaultOnboardingTools) return Promise.resolve();
    if (!bundlePending) {
      bundlePending = new Promise(function (resolve, reject) {
        const script = document.createElement('script');
        script.src = bundleUrl;
        script.onload = function () {
          if (window.vaultOnboardingTools) resolve();
          else reject(new Error('the emergency kit bundle did not register itself'));
        };
        script.onerror = function () {
          reject(new Error('the emergency kit bundle failed to load'));
        };
        document.head.appendChild(script);
      }).catch(function (err) {
        bundlePending = null;
        throw err;
      });
    }
    return bundlePending;
  }

  // Blocks of four, the spelling the sheet prints and a hand can copy.
  // crockfordDecode ignores the hyphens, so this is also what a device
  // remembers: exactly what the user could retype.
  function grouped(secretText) {
    return (secretText.match(/.{1,4}/g) || []).join('-');
  }

  // Synchronous on purpose: the bundle has to be there already, so a click
  // either saves a file or throws where the caller can say so.
  function download(kit) {
    const blob = window.vaultOnboardingTools.buildEmergencyKitPdf({
      email: kit.email,
      serverUrl: window.location.origin,
      secretText: grouped(kit.secretText),
      // The date tells two kits of one account apart, so it is the day its
      // owner saved it on, not the day it was in UTC.
      createdAt: window.userTzDayKey(new Date()),
    });
    window.downloadBlob(blob, 'vault-emergency-kit.pdf');
  }

  return { load: load, grouped: grouped, download: download };
})();
