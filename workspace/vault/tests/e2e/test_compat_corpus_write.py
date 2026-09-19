"""Writes the frozen compatibility corpus. Runs on demand, never in CI.

The corpus has to come out of a real browser: a corpus produced by the Python
reference would only prove the reference agrees with itself, and the node:vm
harness has already been caught feeding the bundle data its own realm built.

Everything the UI exposes is written through the UI. The three things it does
not expose - an entry's notes, a custom: field, and the description of the
vault onboarding creates - go through window.vaultCrypto / vaultApi /
vaultSession in the same page, on the session the UI just opened. That is
still the real client write path; it is the form that is bypassed, never the
crypto. Each derives associated-data strings of its own
(v1|entry-field|<uuid>|notes, v1|entry-field|<uuid>|custom:...,
v1|vault-field|<uuid>|description), which is exactly what a corpus that
stopped at the form would leave unguarded.
"""

import contextlib
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
from .compat_scripts import (
    DESCRIBE_FIRST_VAULT,
    NOTED_ENTRY,
    READ_EVERYTHING,
    WRITE_NOTED_ENTRY,
)
from .test_browser import GOOD_PASSWORD, PANEL, SIDEBAR, VaultBrowserCase

WRITE = os.environ.get("VAULT_COMPAT_CORPUS_WRITE") == "1"
VERSION = "v1"
ARCHIVE_PASSPHRASE = "corpus fige sept huit neuf dix onze douze"

# create_user's default, which the shared harness takes. Written into the
# corpus rather than left implicit: the replays log in with login_as and never
# need it, but a human opening this account does, and the only other way to
# recover it is to crack the frozen hash - which works today solely because
# the test settings pin a fast hasher, and nothing pins that. The walk checks
# the constant against the stored hash, so a changed default fails loudly
# instead of freezing a password that opens nothing.
ACCOUNT_PASSWORD = "pass12345"

# Not the default anything: SHA1/6/30 is what normalizeTotpInput writes for a
# bare secret, so a reader that ignored the parameters and assumed the
# defaults would agree with a corpus built from one. Stored verbatim, which is
# what makes this a claim about the stored string rather than about a parser.
TOTP_URI = (
    "otpauth://totp/Aurora%20Bank:ada"
    "?secret=JBSWY3DPEHPK3PXP&issuer=Aurora%20Bank"
    "&algorithm=SHA256&digits=8&period=45"
)

# Non-ASCII and precomposed, where the entry notes are decomposed: a corpus
# carrying only one normalisation form cannot tell a reader that normalises
# from one that leaves the bytes alone.
PERSONAL_DESCRIPTION = "Coffre personnel \u2014 cl\u00e9s et papiers"
WORK_DESCRIPTION = "Coffre de l'\u00e9quipe \u2014 acc\u00e8s partag\u00e9s"

PERSONAL_VAULT = "Personal"
WORK_VAULT = "Work"
FOLDER_PARENT = "Banking"
FOLDER_CHILD = "Cards"
TAG_NAME = "Finance"
FAVOURITE_ENTRY = "Aurora Bank"
TAGGED_ENTRY = "Aurora Bank"
FOLDERED_ENTRY = "Aurora Card"
TRASHED_ENTRY = "Old Forum"

# Every value the walk types into the form, in one place - and the walk types
# them from here, so "what went in" and "what is expected" cannot drift into
# two transcriptions of each other.
TYPED_ENTRIES = {
    "Aurora Bank": {"username": "ada", "password": "hunter2"},
    "Aurora Card": {"username": "4111", "password": "0000"},
    "Old Forum": {"username": "ada", "password": "letmein"},
    "Work Wiki": {"username": "ada", "password": "s3cret-wiki"},
}

# The account the walk is supposed to have built, named end to end. A guard
# that only checked the shape would pass a write that sealed the wrong bytes -
# and freezing that is the one mistake this whole corpus cannot recover from,
# because all three replays would then confirm it faithfully, forever.
EXPECTED_ACCOUNT = {
    PERSONAL_VAULT: {
        "description": PERSONAL_DESCRIPTION,
        # folder name -> parent folder name, or None at the root.
        "folders": {FOLDER_PARENT: None, FOLDER_CHILD: FOLDER_PARENT},
        "tags": {TAG_NAME},
        "entries": {
            "Aurora Bank": dict(TYPED_ENTRIES["Aurora Bank"], totp=TOTP_URI),
            "Aurora Card": TYPED_ENTRIES["Aurora Card"],
            "Old Forum": TYPED_ENTRIES["Old Forum"],
            NOTED_ENTRY["name"]: NOTED_ENTRY["fields"],
        },
    },
    WORK_VAULT: {
        "description": WORK_DESCRIPTION,
        "folders": {},
        "tags": set(),
        "entries": {"Work Wiki": TYPED_ENTRIES["Work Wiki"]},
    },
}

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


def _refuse_if_published(version: str, root: Path = compat.CORPUS_ROOT) -> Path:
    """The path ``version`` would take, or FileExistsError if it is published."""
    target = root / version
    if target.exists():
        raise FileExistsError(
            f"{target} is published and append-only. A format change adds the "
            f"next version beside it; it never edits this one."
        )
    return target


@contextlib.contextmanager
def _corpus_output(version: str, root: Path = compat.CORPUS_ROOT):
    """A directory to fill, published as ``version`` only if the walk finishes.

    Creating the version directory up front and writing into it as the walk
    goes makes every failure worse than the failure: a selector that times out
    halfway leaves an empty or half-written directory behind, and that
    directory then refuses every later regeneration while making compat.load()
    die on a missing file instead of saying what is wrong. Staging elsewhere
    turns publication into one rename - it either happened or it did not.
    """
    target = _refuse_if_published(version, root)
    staging = Path(tempfile.mkdtemp(prefix=f"vault-corpus-{version}-"))
    try:
        yield staging
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging), str(target))


class CorpusWriteGuardTests(SimpleTestCase):
    """What the generator does with the output directory, without a browser.

    The refusal is not politeness: the append-only test of task 5 compares
    committed hashes, so an overwrite would be found only after it had already
    destroyed the corpus in the working tree.
    """

    def _scratch_root(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root

    def test_a_finished_walk_publishes_what_it_staged(self):
        root = self._scratch_root()
        with _corpus_output("v99", root=root) as staging:
            self.assertFalse(
                (root / "v99").exists(), "the version appeared before it was written"
            )
            (staging / "rows.json").write_text("[]", encoding="utf-8")
        self.assertEqual((root / "v99" / "rows.json").read_text(encoding="utf-8"), "[]")

    def test_a_failed_walk_publishes_nothing_and_leaves_nothing(self):
        root = self._scratch_root()
        staged = None
        with self.assertRaises(RuntimeError):
            with _corpus_output("v99", root=root) as staging:
                staged = staging
                (staging / "rows.json").write_text("[]", encoding="utf-8")
                raise RuntimeError("a selector timed out halfway through the walk")
        # Both halves. A version directory left behind refuses every later
        # regeneration and makes the corpus unloadable; a staging directory
        # left behind is a slow leak nobody would ever look for.
        self.assertFalse((root / "v99").exists(), "a failed walk published a version")
        self.assertFalse(staged.exists(), "the staging directory was left behind")

    def test_writing_into_a_published_version_is_refused(self):
        # The real root and the real version: the refusal is only worth
        # anything against the directory a rerun would actually destroy.
        with self.assertRaises(FileExistsError):
            with _corpus_output(VERSION):
                pass


@unittest.skipUnless(WRITE, "set VAULT_COMPAT_CORPUS_WRITE=1 to rewrite the corpus")
class CorpusWriteWalk(VaultBrowserCase):
    """One run, one corpus, then never again for this version."""

    @classmethod
    def setUpClass(cls):
        # Before the browser starts. Chromium plus an onboarding costs the
        # better part of a minute, and there is nothing to learn from spending
        # it to fail on a directory that was already there when the run began.
        _refuse_if_published(VERSION)
        super().setUpClass()

    # ---- the pieces of the account the shared harness has no walk for -----

    def _create_typed_entry(self, name):
        fields = TYPED_ENTRIES[name]
        self._create_entry(name, fields["username"], fields["password"])

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

    def _new_vault(self, name, description):
        self.page.click("[data-testid='vault-switcher']")
        self.page.click("text=New vault")
        self.page.wait_for_selector(".modal-box input[type=text]")
        # The dialog's two text fields, in document order: the icon picker it
        # includes contributes none, so nth is unambiguous here.
        self.page.fill(".modal-box input[type=text] >> nth=0", name)
        self.page.fill(".modal-box input[type=text] >> nth=1", description)
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
        """Everything the corpus exists to cover, named one at a time.

        This half is about coverage - that the account carries a trashed row,
        a favourite, a nested folder at all. It survives a renamed entry on
        purpose; the half below is the one that pins the values.
        """
        vaults = manifest["vaults"]
        self.assertEqual(len(vaults), 2, "the corpus needs two vaults")

        # Every vault, not merely one: the description is sealed under an
        # associated-data string of its own, and a vault that left it empty
        # would take that string out of the corpus on its own row.
        descriptions = [vault["description"] for vault in vaults]
        for description in descriptions:
            self.assertTrue(
                description, f"a vault has no description: {descriptions!r}"
            )
        self.assertTrue(
            any(not text.isascii() for text in descriptions),
            f"no description is non-ASCII, descriptions are {descriptions!r}",
        )

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
            any("\u0301" in note for note in noted),
            f"no note is in NFD, notes are {noted!r}",
        )

        keys = [
            entry["fields"]["totp"] for entry in entries if "totp" in entry["fields"]
        ]
        self.assertEqual(keys, [TOTP_URI], "the authenticator key did not round-trip")

    def _assert_the_manifest_matches_what_was_written(self, manifest):
        """The manifest against the values that went in, one by one.

        The half the corpus cannot do without. A shape check passes a write
        that sealed the wrong bytes just as readily as a correct one, and a
        wrong corpus is worse than no corpus: it is published append-only, so
        the three replays spend every future commit confirming the mistake.
        """
        by_name = {vault["name"]: vault for vault in manifest["vaults"]}
        self.assertEqual(sorted(by_name), sorted(EXPECTED_ACCOUNT))

        for vault_name, expected in EXPECTED_ACCOUNT.items():
            vault = by_name[vault_name]
            self.assertEqual(vault["description"], expected["description"], vault_name)

            folders = {folder["name"]: folder for folder in vault["folders"]}
            self.assertEqual(sorted(folders), sorted(expected["folders"]), vault_name)
            for folder_name, parent_name in expected["folders"].items():
                # The tree by name: the manifest carries UUIDs, and resolving
                # them here is what makes "Cards is inside Banking" a claim
                # rather than "Cards has some parent".
                expected_parent = folders[parent_name]["uuid"] if parent_name else None
                self.assertEqual(
                    folders[folder_name]["parent"], expected_parent, folder_name
                )

            self.assertEqual(
                {tag["name"] for tag in vault["tags"]}, expected["tags"], vault_name
            )

            entries = {entry["name"]: entry for entry in vault["entries"]}
            self.assertEqual(sorted(entries), sorted(expected["entries"]), vault_name)
            for entry_name, fields in expected["entries"].items():
                self.assertEqual(entries[entry_name]["fields"], fields, entry_name)

        personal = by_name[PERSONAL_VAULT]
        folders = {folder["name"]: folder for folder in personal["folders"]}
        entries = {entry["name"]: entry for entry in personal["entries"]}

        self.assertEqual(
            entries[FOLDERED_ENTRY]["folder"], folders[FOLDER_CHILD]["uuid"]
        )
        tag_uuid = personal["tags"][0]["uuid"]
        self.assertEqual(entries[TAGGED_ENTRY]["tags"], [tag_uuid])

        every = [entry for vault in manifest["vaults"] for entry in vault["entries"]]
        self.assertEqual(
            {e["name"] for e in every if e["is_favorite"]}, {FAVOURITE_ENTRY}
        )
        self.assertEqual({e["name"] for e in every if e["trashed"]}, {TRASHED_ENTRY})
        self.assertEqual({e["name"] for e in every if e["tags"]}, {TAGGED_ENTRY})
        # Notes on the one entry that was given notes, and nowhere else: an
        # empty string is what an entry without them stores, and a reader
        # inventing one somewhere would show up here.
        self.assertEqual(
            {e["name"]: e["notes"] for e in every if e["notes"]},
            {NOTED_ENTRY["name"]: NOTED_ENTRY["notes"]},
        )

    def test_write_the_corpus(self):
        with _corpus_output(VERSION) as target:
            self._walk_and_write(target)

    def _walk_and_write(self, target):
        self._open_vault()  # onboarding + the first vault, by the UI

        # The password the corpus writes down is the one the account has. A
        # changed create_user default would otherwise be frozen as a password
        # that opens nothing, and nobody would find out until they tried.
        self.assertTrue(
            self.user.check_password(ACCOUNT_PASSWORD),
            "the account password written into the corpus is not the account's",
        )

        # Onboarding names the first vault and describes nothing, and no
        # dialog renders the field afterwards - so this one row needs the
        # update helper the rename dialog itself calls.
        described = self.page.evaluate(DESCRIBE_FIRST_VAULT, PERSONAL_DESCRIPTION)
        self.assertEqual(described["status"], 200, described.get("reason"))

        self._create_typed_entry(FAVOURITE_ENTRY)
        self._add_totp_uri(FAVOURITE_ENTRY, TOTP_URI)
        self._favourite(FAVOURITE_ENTRY)
        self._new_tag(TAG_NAME)
        self._wear_tag(TAGGED_ENTRY, TAG_NAME)

        # A folder inside a folder, and a row living in the inner one: parent
        # and position are plaintext and covered by nothing but the folder
        # signature, so a tree one level deep is the shallowest corpus that
        # exercises them.
        self._new_folder(FOLDER_PARENT)
        self._open_folder(FOLDER_PARENT)
        self._new_folder(FOLDER_CHILD)
        self._open_folder(FOLDER_CHILD)
        self._create_typed_entry(FOLDERED_ENTRY)
        self._back_to_all_entries()

        self._create_typed_entry(TRASHED_ENTRY)
        self._trash(TRASHED_ENTRY)

        # The notes and the custom field, on the session the UI just opened.
        written = self.page.evaluate(WRITE_NOTED_ENTRY, False)
        self.assertEqual(written["status"], 201, written.get("reason"))

        # listVaults()[0] is the first vault by created_at, so the scripted
        # entry above landed in Personal whatever is on screen now.
        self._new_vault(WORK_VAULT, WORK_DESCRIPTION)
        self._create_typed_entry("Work Wiki")

        manifest = self.page.evaluate(READ_EVERYTHING)
        self._assert_the_manifest_is_whole(manifest)
        self._assert_the_manifest_matches_what_was_written(manifest)

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
                    "account_password": ACCOUNT_PASSWORD,
                    "vault_master_password": GOOD_PASSWORD,
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
