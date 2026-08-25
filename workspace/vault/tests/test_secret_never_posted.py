"""The recovery secret must never reach a request body.

A server-side PDF pipeline, or a well-meant "let us keep a copy of your kit"
endpoint, would break the module's whole promise in one line - and nothing
else in the suite would notice. This is a test rather than a workflow step so
it runs locally too, and so its failure names the file to fix.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

VAULT_JS = Path("workspace/vault/ui/static/vault/ui/js")
FIXTURE = Path("workspace/vault/tests/fixtures/violation_sample.js")

# The names the module holds a recovery secret under. A new alias has to be
# added here deliberately, which is the point: the catalogue is the contract.
SECRET_NAMES = r"(secretKey|secret_key|secretText|secretBytes|recoverySecret)"

# An outgoing call and a secret name close enough together to be one
# statement. The module wraps fetch in its own post(), so listing the browser
# APIs alone let a real leak through when this guard was first written -
# JSON.stringify is here for the same reason, since a body is built with it
# whatever carries it afterwards. Deliberately blunt: a false positive costs a
# rewritten line, a false negative costs the zero-knowledge promise.
OUTGOING = re.compile(
    rf"(fetch|\$ajax|XMLHttpRequest|FormData|sendBeacon|JSON\.stringify"
    rf"|\.(post|put|patch|send)\s*\()"
    rf"[^;]{{0,400}}?{SECRET_NAMES}",
    re.DOTALL,
)


def scan(source: str) -> list[str]:
    return [match.group(0) for match in OUTGOING.finditer(source)]


class SecretNeverPostedTests(SimpleTestCase):
    def test_no_module_script_puts_the_secret_in_a_request(self):
        offenders = {}
        for path in sorted(VAULT_JS.rglob("*.js")):
            if "vendor" in path.parts:
                continue
            hits = scan(path.read_text(encoding="utf-8"))
            if hits:
                offenders[str(path)] = hits
        self.assertEqual(
            offenders,
            {},
            "a recovery secret reaches an outgoing request: the vault's "
            "zero-knowledge promise is that it never leaves the device",
        )

    def test_the_guard_catches_a_deliberate_violation(self):
        """Without this, a scan that matched nothing at all would look green."""
        self.assertTrue(scan(FIXTURE.read_text(encoding="utf-8")))

    def test_the_guard_reads_the_module_at_all(self):
        """A typo in the path would make the first test vacuously true."""
        scripts = [p for p in VAULT_JS.rglob("*.js") if "vendor" not in p.parts]
        self.assertTrue(scripts)
