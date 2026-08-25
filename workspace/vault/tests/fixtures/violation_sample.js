// Not production code, and not loaded by anything. This file exists to be
// caught by test_secret_never_posted.py: a guard nobody has watched fail is a
// guard nobody knows works. Deleting it silently disarms the check.
export function leakTheKit(secretText) {
  return fetch('/api/v1/vault/account/finalize', {
    method: 'POST',
    body: JSON.stringify({ secretText }),
  });
}
