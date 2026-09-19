"""The corpus has to be there at all, and it has to be readable.

Everything else in this module proves an implementation against another
implementation. None of that notices a format change: the two sides move
together, agree with each other, and every vault written before the change
stops opening. Only data nobody may rewrite catches that, so the first claim
this file makes is that such data exists.

The append-only guard below closes a hole its own review found: a presence
check (a file exists, an archive starts with the right magic) still passes
for a corpus that has been quietly emptied. Hashing every published file
against a committed manifest, and refusing an empty manifest, is what makes
"unchanged" mean something.
"""

import hashlib
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from . import compat, test_compat_reference, test_compat_server
from .e2e import test_compat_browser


class CorpusPresenceTests(SimpleTestCase):
    def test_at_least_one_version_is_published(self):
        # The whole issue is that no old data exists to replay. An empty
        # corpus root is that state, and it must not pass silently.
        self.assertEqual(compat.versions(), ["v1"])

    def test_every_published_version_loads(self):
        # versions() only reads directory names. A version whose four files
        # never landed would still be listed above, so the corpus that the
        # replays actually open is the one asserted here.
        for version in compat.versions():
            with self.subTest(version=version):
                files = compat.load(version)
                self.assertTrue(files.rows.is_file())
                self.assertTrue(files.archive.startswith(b"VLTARCH"))
                # Both passwords, named apart: one opens the Django
                # account, the other opens the vault, and a corpus that
                # carried one key called "password" would read as a
                # single credential pair that it never was.
                self.assertIn("account_password", files.credentials)
                self.assertIn("vault_master_password", files.credentials)
                self.assertTrue(files.manifest["vaults"])


def _read_sums(path: Path) -> dict[str, str]:
    """Parse a `sha256sum`-format manifest into {filename: hex digest}.

    A missing or unreadable manifest is a loud failure by construction:
    `read_text` raises straight through, there is no `except` here to turn
    it into a skip or an empty result.
    """
    sums = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, name = line.partition("  ")
        sums[name.removeprefix("*")] = digest
    return sums


def _verify_corpus_version(root: Path) -> None:
    """Raise AssertionError unless every file under `root` matches
    `root/SHA256SUMS`, and the manifest names at least one file.

    Plain `assert`, not `self.assertX`: this is exercised both as the real
    guard (AppendOnlyTests, against the committed corpus) and directly
    against a synthetic directory (AppendOnlyGuardSelfTests), and it has no
    TestCase to hang assertions off in the second case.

    SHA256SUMS does not hash itself. It describes the four payload files;
    a change to the manifest is a change to a file git already tracks and
    diffs on its own, and asking it to also cover its own bytes would only
    add a bootstrapping problem (the hash of the file has to be written
    into the file whose hash it is) for no file this guard doesn't already
    catch some other way.
    """
    expected = _read_sums(root / "SHA256SUMS")
    # {} == {} for an emptied directory and an emptied manifest is exactly
    # the presence-only hole this guard exists to close - refuse it
    # explicitly rather than relying on there always being files to diff.
    assert expected, f"{root / 'SHA256SUMS'} names no files"
    actual = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.iterdir())
        if path.name != "SHA256SUMS"
    }
    assert actual == expected, (
        f"{root} is published and append-only. A format change adds "
        f"the next version beside it; it never edits this one."
    )


class AppendOnlyTests(SimpleTestCase):
    def test_no_published_file_has_changed(self):
        self.assertTrue(compat.versions(), "no corpus version is published")
        for version in compat.versions():
            with self.subTest(version=version):
                _verify_corpus_version(compat.CORPUS_ROOT / version)

    def test_every_version_is_covered_by_the_replays(self):
        # A corpus no replay reads is a directory, not a safety net. Every
        # published version must be read by all three: the Python
        # reference, the server's own verifiers, and a real browser.
        covered = (
            set(test_compat_reference.COVERED)
            & set(test_compat_server.COVERED)
            & set(test_compat_browser.COVERED)
        )
        self.assertEqual(set(compat.versions()), covered)


class AppendOnlyGuardSelfTests(SimpleTestCase):
    """Proves `_verify_corpus_version` closes the hole Task 1's review
    found, without touching the real corpus to do it.
    """

    def test_an_empty_directory_with_an_empty_manifest_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SHA256SUMS").write_text("", encoding="utf-8")
            with self.assertRaises(AssertionError):
                _verify_corpus_version(root)

    def test_a_file_added_without_being_hashed_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"hello")
            (root / "SHA256SUMS").write_text(
                f"{hashlib.sha256(b'hello').hexdigest()}  a.txt\n", encoding="utf-8"
            )
            (root / "b.txt").write_bytes(b"surprise")  # added, never hashed
            with self.assertRaises(AssertionError):
                _verify_corpus_version(root)

    def test_a_file_removed_without_updating_the_manifest_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"hello")
            (root / "b.txt").write_bytes(b"world")
            (root / "SHA256SUMS").write_text(
                f"{hashlib.sha256(b'hello').hexdigest()}  a.txt\n"
                f"{hashlib.sha256(b'world').hexdigest()}  b.txt\n",
                encoding="utf-8",
            )
            (root / "b.txt").unlink()  # removed, manifest still lists it
            with self.assertRaises(AssertionError):
                _verify_corpus_version(root)

    def test_a_missing_manifest_raises_rather_than_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"hello")
            with self.assertRaises(FileNotFoundError):
                _verify_corpus_version(root)
