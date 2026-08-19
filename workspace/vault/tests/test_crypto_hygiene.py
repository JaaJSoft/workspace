"""Source-level guards on the cryptography, checked rather than trusted.

Two properties this module rests on are invisible at runtime: that no
application code can reach the reference implementation, and that nothing in
the cryptographic path draws from a pseudo-random generator. Both hold today
because someone was careful. These tests are what makes them hold tomorrow.
"""

import ast
import pathlib
import unittest.mock

from django.test import SimpleTestCase

from workspace.vault.tests.reference import encoding

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKSPACE = REPO_ROOT / "workspace"
REFERENCE_PACKAGE = "workspace.vault.tests.reference"
VAULT_TESTS = WORKSPACE / "vault" / "tests"


def _module_name(path: pathlib.Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Absolute module names imported by *path*, relative imports resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_name(path).rsplit(".", 1)[0]
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                # level 1 is the containing package, each extra level climbs one.
                base = base[: len(base) - node.level + 1]
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            imported.add(prefix)
            imported.update(f"{prefix}.{alias.name}" for alias in node.names)
    return imported


class ReferenceIsolationTests(SimpleTestCase):
    """The reference implementation must stay unreachable from the application.

    It can derive account keys, unwrap private keys and decrypt every field. A
    server-side path able to import it would be a server-side path able to
    decrypt, which is the one property this module exists to deny. Nothing in
    Python prevents the import; this test does.
    """

    def test_no_module_outside_the_vault_tests_imports_the_reference(self):
        offenders = []
        for path in WORKSPACE.rglob("*.py"):
            if VAULT_TESTS in path.parents or path == VAULT_TESTS:
                continue
            if any(
                name == REFERENCE_PACKAGE or name.startswith(f"{REFERENCE_PACKAGE}.")
                for name in _imported_modules(path)
            ):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            offenders,
            [],
            f"these modules import the test-only cryptographic reference: {offenders}",
        )

    def test_the_guard_would_catch_an_import(self):
        """A guard that cannot fire is decorative: this proves the walker sees
        the import it is looking for, in the form an offender would write it.
        """
        sample = VAULT_TESTS / "test_crypto_vectors.py"
        self.assertIn(
            f"{REFERENCE_PACKAGE}.generate_vectors",
            _imported_modules(sample),
        )


class RandomnessSourceTests(SimpleTestCase):
    JS_SOURCES = sorted(
        (REPO_ROOT / "scripts" / "frontend" / "src" / "vault").glob("*.js")
    )
    PY_SOURCES = sorted(
        path
        for path in (VAULT_TESTS / "reference").glob("*.py")
        # The corpus generator draws test inputs, not key material; seeding it
        # is what makes a fuzzing failure reproducible.
        if path.name != "generate_fuzz_corpus.py"
    )

    def test_the_browser_sources_never_reach_for_math_random(self):
        self.assertTrue(self.JS_SOURCES, "no JavaScript source found to scan")
        offenders = [
            path.name
            for path in self.JS_SOURCES
            if "Math.random" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [], f"Math.random in {offenders}")

    def test_the_reference_never_reaches_for_the_random_module(self):
        self.assertTrue(self.PY_SOURCES, "no Python source found to scan")
        offenders = []
        for path in self.PY_SOURCES:
            imported = _imported_modules(path)
            if "random" in imported:
                offenders.append(path.name)
        self.assertEqual(offenders, [], f"the random module is imported by {offenders}")

    def test_a_short_read_from_the_csprng_is_refused(self):
        """The guard exists because a partial fill would silently weaken every
        key derived from it, and nothing downstream would notice.
        """
        with unittest.mock.patch.object(
            encoding.secrets, "token_bytes", return_value=b"\x00" * 8
        ):
            with self.assertRaises(ValueError):
                encoding.random_bytes(32)

    def test_a_full_read_is_returned_unchanged(self):
        self.assertEqual(len(encoding.random_bytes(32)), 32)
        self.assertNotEqual(encoding.random_bytes(32), encoding.random_bytes(32))
