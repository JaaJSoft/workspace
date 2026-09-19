"""Writes the frozen compatibility corpus. Runs on demand, never in CI.

The corpus has to come out of a real browser: a corpus produced by the Python
reference would only prove the reference agrees with itself, and the node:vm
harness has already been caught feeding the bundle data its own realm built.

Everything the UI exposes is written through the UI. The two things it does
not expose - an entry's notes and a custom: field - go through
window.vaultCrypto / vaultApi / vaultSession in the same page, on the session
the UI just opened. That is still the real client write path; it is the form
that is bypassed, never the crypto. Both derive associated-data strings of
their own (v1|entry-field|<uuid>|notes, v1|entry-field|<uuid>|custom:...),
which is exactly what a corpus that stopped at the form would leave unguarded.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from django.core import management
from django.test import SimpleTestCase

from workspace.vault.models import VaultEntry

from .. import compat
from .compat_scripts import READ_EVERYTHING, WRITE_NOTED_ENTRY
from .test_browser import GOOD_PASSWORD, PANEL, SIDEBAR, VaultBrowserCase

WRITE = os.environ.get("VAULT_COMPAT_CORPUS_WRITE") == "1"
VERSION = "v1"
ARCHIVE_PASSPHRASE = "corpus fige sept huit neuf dix onze douze"

# Not the default anything: SHA1/6/30 is what normalizeTotpInput writes for a
# bare secret, so a reader that ignored the parameters and assumed the
# defaults would agree with a corpus built from one. Stored verbatim, which is
# what makes this a claim about the stored string rather than about a parser.
TOTP_URI = (
    "otpauth://totp/Aurora%20Bank:ada"
    "?secret=JBSWY3DPEHPK3PXP&issuer=Aurora%20Bank"
    "&algorithm=SHA256&digits=8&period=45"
)

# Dumped in dependency order so loaddata can insert without deferring.
DUMP_MODELS = [
    "auth.User",
    "vault.AccountIdentity",
    "vault.Vault",
    "vault.VaultKeyWrap",
    "vault.VaultFolder",
    "vault.VaultTag",
    "vault.VaultEntry",
    "vault.EntryField",
]


def _prepare_output_dir(version: str, root: Path = compat.CORPUS_ROOT) -> Path:
    """The directory to write, or FileExistsError if it is already published."""
    target = root / version
    if target.exists():
        raise FileExistsError(
            f"{target} is published and append-only. A format change adds the "
            f"next version beside it; it never edits this one."
        )
    target.mkdir(parents=True)
    return target


class CorpusWriteGuardTests(SimpleTestCase):
    """The generator refuses a version that already exists.

    Not politeness: the append-only test of task 5 compares committed hashes,
    so an overwrite would be found only after it had already destroyed the
    corpus in the working tree.
    """

    def test_a_fresh_version_gets_its_directory(self):
        # Under a root of its own, so the positive half never touches the
        # published corpus. A version name that does not exist yet is the only
        # thing this half is about.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        target = _prepare_output_dir("v99", root=root)
        self.assertEqual(target, root / "v99")
        self.assertTrue(target.is_dir())

    def test_writing_into_a_published_version_is_refused(self):
        # The real root and the real version: the refusal is only worth
        # anything against the directory a rerun would actually destroy.
        with self.assertRaises(FileExistsError):
            _prepare_output_dir(VERSION)


@unittest.skipUnless(WRITE, "set VAULT_COMPAT_CORPUS_WRITE=1 to rewrite the corpus")
class CorpusWriteWalk(VaultBrowserCase):
    """One run, one corpus, then never again for this version."""

    # ---- the pieces of the account the shared harness has no walk for -----

    def _add_totp_uri(self, name, uri):
        """Put a complete otpauth:// address on an entry, through the form.

        The harness' own helper pins the single-entry account it was written
        for and types a bare secret; this account holds several rows and needs
        the parameters the bare form cannot express.
        """
        self.page.click(f"tbody tr:has-text('{name}')")
        self.page.locator(PANEL).get_by_role("button", name="Edit").click()
        self.page.wait_for_selector(".modal-box")
        self.page.get_by_role("button", name="Add").click()
        self.page.fill(".modal-box input[placeholder='Key or otpauth:// address']", uri)
        self.page.click(".modal-box button:has-text('Save')")
        self.page.wait_for_selector(
            ".modal-box:has-text('Save')", state="hidden", timeout=30000
        )
        # The dialog closes on a write that carried no key just as readily as
        # on one that did, so the row is what says the field landed.
        self.assertEqual(
            VaultEntry.objects.filter(fields__field_id="totp").count(),
            1,
            "the authenticator key was not written",
        )

    def _new_folder(self, name):
        self.page.get_by_role("button", name="New", exact=True).click()
        # .first: the file picker dialog the shared layout mounts carries a
        # "New folder" button of its own.
        self.page.get_by_role("button", name="New folder").first.click()
        self.page.fill(".modal-box input[type=text]", name)
        self.page.click(".modal-box button:has-text('Create')")
        self.page.wait_for_selector(f"tbody tr:has-text('{name}')", timeout=30000)

    def _open_folder(self, name):
        self.page.click(f"tbody tr:has-text('{name}')")
        # The heading, not the listing: an empty folder shows nothing at all,
        # so "the row went away" is the only other signal and it also fires
        # when the click landed on the wrong row.
        self.page.wait_for_selector(f"h1:text-is('{name}')", timeout=30000)

    def _back_to_all_entries(self):
        self.page.locator(SIDEBAR).get_by_role("button", name="All entries").click()
        self.page.wait_for_selector("h1:text-is('All entries')", timeout=30000)

    def _favourite(self, name):
        self.page.locator("tbody tr", has_text=name).click(button="right")
        self.page.locator("#entry-context-menu").get_by_text(
            "Add to favourites"
        ).click()
        self.page.wait_for_selector(
            f"tbody tr:has-text('{name}') .text-warning", timeout=30000
        )

    def _new_tag(self, name):
        self.page.get_by_role("button", name="New tag").click()
        self.page.fill(".modal-box input[type=text]", name)
        self.page.click(".modal-box button:has-text('Create')")
        self.page.wait_for_selector("aside tag-chip", timeout=30000)

    def _wear_tag(self, entry_name, tag_name):
        self.page.click(f"tbody tr:has-text('{entry_name}')")
        self.page.click(f"{PANEL} button:has-text('Edit')")
        self.page.locator(".modal-box").get_by_text(tag_name).click()
        self.page.click(".modal-box button:has-text('Save')")
        self.page.wait_for_selector(
            f"tbody tr:has-text('{entry_name}') tag-chip", timeout=30000
        )

    def _trash(self, name):
        self.page.click(f"tbody tr:has-text('{name}')")
        self.page.click(f"{PANEL} button[aria-label='Move to trash']")
        self.page.wait_for_selector(f"tbody tr:has-text('{name}')", state="detached")

    def _new_vault(self, name):
        self.page.click("[data-testid='vault-switcher']")
        self.page.click("text=New vault")
        self.page.wait_for_selector(".modal-box input[type=text]")
        self.page.fill(".modal-box input[type=text] >> nth=0", name)
        self.page.click(".modal-box button:has-text('Create')")
        self._wait_for_switcher_named(name)

    def _export_archive(self, passphrase):
        """The sealed archive, taken from the browser's own download.

        A phrase typed rather than drawn: the corpus has to name the phrase in
        credentials.json, and the generated path would hand back whatever the
        dice produced that day.
        """
        self._open_export()
        self.page.fill("#export-passphrase", passphrase)
        self.page.wait_for_selector("#export-confirm", state="attached", timeout=10000)
        self.page.fill("#export-confirm", passphrase)
        self.page.check("[data-testid='export-own-phrase-ack']")
        with self.page.expect_download(timeout=180000) as download:
            self.page.click("[data-testid='export-run']")
        blob = download.value.path().read_bytes()
        self.assertTrue(blob.startswith(b"VLTARCH"), "not an archive at all")
        return blob

    # ---- the account this corpus is ---------------------------------------

    def _assert_the_manifest_is_whole(self, manifest):
        """Everything the corpus exists to guard, named one at a time.

        A silent selector produces a manifest that parses, so the run has to
        refuse to write a thin one rather than leave three future replays
        agreeing about an empty account.
        """
        vaults = manifest["vaults"]
        self.assertEqual(len(vaults), 2, "the corpus needs two vaults")

        entries = [entry for vault in vaults for entry in vault["entries"]]
        folders = [folder for vault in vaults for folder in vault["folders"]]
        tags = [tag for vault in vaults for tag in vault["tags"]]

        self.assertTrue(
            any(folder["parent"] for folder in folders), "no folder has a parent"
        )
        self.assertTrue(tags, "no tag was created")
        self.assertTrue(any(entry["tags"] for entry in entries), "no entry wears a tag")
        self.assertTrue(
            any(entry["trashed"] for entry in entries), "nothing is in the trash"
        )
        self.assertTrue(
            any(entry["is_favorite"] for entry in entries), "nothing is a favourite"
        )
        self.assertTrue(
            any(entry["folder"] for entry in entries), "no entry is in a folder"
        )
        self.assertTrue(
            any("custom:pin" in entry["fields"] for entry in entries),
            "no entry carries a custom field",
        )

        noted = [entry["notes"] for entry in entries if entry["notes"]]
        self.assertTrue(noted, "no entry carries notes")
        # The decomposed form, byte for byte. A reader that normalised would
        # hand back the same word in NFC, which renders identically and seals
        # differently - the exact break a corpus is for.
        self.assertTrue(
            any("́" in note for note in noted),
            f"no note is in NFD, notes are {noted!r}",
        )

        keys = [
            entry["fields"]["totp"] for entry in entries if "totp" in entry["fields"]
        ]
        self.assertEqual(keys, [TOTP_URI], "the authenticator key did not round-trip")

    def test_write_the_corpus(self):
        target = _prepare_output_dir(VERSION)

        self._open_vault()  # onboarding + the first vault, by the UI

        self._create_entry("Aurora Bank", "ada", "hunter2")
        self._add_totp_uri("Aurora Bank", TOTP_URI)
        self._favourite("Aurora Bank")
        self._new_tag("Finance")
        self._wear_tag("Aurora Bank", "Finance")

        # A folder inside a folder, and a row living in the inner one: parent
        # and position are plaintext and covered by nothing but the folder
        # signature, so a tree one level deep is the shallowest corpus that
        # exercises them.
        self._new_folder("Banking")
        self._open_folder("Banking")
        self._new_folder("Cards")
        self._open_folder("Cards")
        self._create_entry("Aurora Card", "4111", "0000")
        self._back_to_all_entries()

        self._create_entry("Old Forum", "ada", "letmein")
        self._trash("Old Forum")

        # The notes and the custom field, on the session the UI just opened.
        written = self.page.evaluate(WRITE_NOTED_ENTRY, False)
        self.assertEqual(written["status"], 201, written.get("reason"))

        # listVaults()[0] is the first vault by created_at, so the scripted
        # entry above landed in Personal whatever is on screen now.
        self._new_vault("Work")
        self._create_entry("Work Wiki", "ada", "s3cret-wiki")

        manifest = self.page.evaluate(READ_EVERYTHING)
        self._assert_the_manifest_is_whole(manifest)

        archive = self._export_archive(ARCHIVE_PASSPHRASE)
        (target / "archive.bin").write_bytes(archive)

        # newline="\n" on every text file here, and on the two below: the
        # corpus is read back byte for byte, and the platform default would
        # write a corpus whose line endings depend on where it was generated.
        with (target / "rows.json").open("w", encoding="utf-8", newline="\n") as handle:
            management.call_command("dumpdata", *DUMP_MODELS, indent=2, stdout=handle)
        (target / "credentials.json").write_text(
            json.dumps(
                {
                    "note": "Frozen test account. These are not live secrets.",
                    "username": self.user.username,
                    "master_password": GOOD_PASSWORD,
                    "secret_key": self.secret,
                    "archive_passphrase": ARCHIVE_PASSPHRASE,
                },
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        (target / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
