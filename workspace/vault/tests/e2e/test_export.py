"""The export, driven in a real browser.

Node is not a browser. The unit tests read the mixin's state without ever
rendering the dialog, and this module has already shipped a revealed password
that never appeared on screen behind twenty-one green ones. Everything below
is a property no amount of state-reading can establish:

  - a file reaches the user at all, from a click on a sidebar row;
  - nothing plaintext leaves the page while it is built;
  - a lock takes the dialog and the passphrase with it;
  - the plaintext format warns first, and a warning that cannot be shown is
    a refusal rather than a silent yes.

The last one is the reason this file exists. ``dialogs.js`` declares its
component with a top-level ``const``, so the bare name resolves and
``window.AppDialog`` does not - and the ``node:vm`` loader the JS suite runs
on sets ``sandbox.window = sandbox``, which makes the two lookups the same
access by construction. No unit test in this repository can tell them apart.
A browser can, and does, below.
"""

import re

from .test_browser import VaultBrowserCase

SEEDED_ENTRY_NAME = "GitHub"
SEEDED_ENTRY_LOGIN = "octocat"
# Distinctive on purpose: every walk here asserts this string is absent from a
# request body or from the page, and a common word would pass vacuously.
SEEDED_ENTRY_PASSWORD = "trombone-sunset-91"
KNOWN_PASSPHRASE = "the-phrase-a-person-typed-42"

# The shared confirm dialog's own ids, read off
# common/templates/ui/partials/dialogs.html. That partial serves six modules
# and gets no testid for this.
CONFIRM = "#app-dialog-confirm"
CONFIRM_CANCEL = "#app-dialog-confirm-cancel"

# The script that declares the confirm dialog. Serving it empty is how a walk
# reproduces "the warning cannot be shown" without touching the page's code.
DIALOGS_SCRIPT = "**/ui/js/dialogs.js"


class ExportWalkTests(VaultBrowserCase):
    """The export dialog, from the sidebar row to the file."""

    def setUp(self):
        super().setUp()
        # Collected for the walks whose claim is that *no* file was built.
        # expect_download only proves the positive case; an empty list is the
        # only way to assert the negative one.
        self._downloads = []
        # A lambda, not the list's own bound method: Playwright stamps an
        # attribute on the handler it is given, and a builtin has nowhere to
        # put one.
        self.page.on("download", lambda download: self._downloads.append(download))

    # ---- reading what the page did ---------------------------------------

    def _drain_events(self):
        """Block until every event the page has already raised has arrived.

        Playwright delivers one page's events in wire order over a single
        connection, so a request the walk makes itself is a barrier: once its
        event is here, everything the page raised before it is here too.

        Every negative assertion below needs this. A leak fired moments before
        a download, or a download started moments before a click was refused,
        is still in flight when the assertions run - and an absence asserted
        too early is an absence that proves nothing. This is the race that
        made the first version of the leak walk pass a deliberate leak.
        """
        with self.page.expect_request("**/api/v1/vault/vaults"):
            self.page.evaluate("() => { window.vaultApi.listVaults(); }")

    def _component_strings(self):
        """Every string the browser component is holding, as one blob.

        Read off Alpine rather than off the DOM. A passphrase lives in a JS
        string that no serialisation of the page ever reaches, so the HTML is
        the wrong place to look for it: the HTML says whether a secret was
        *rendered*, this says whether one is still *held*.
        """
        blob = self.page.evaluate(
            """() => {
                const root = document.querySelector('[x-data="vaultBrowser()"]');
                // The names come off the data stack and the values off
                // $data. Object.keys on the $data proxy answers an empty
                // list - its target is a bare {} and the merge is done in the
                // traps - so enumerating it is how this read silently
                // inspects nothing at all.
                const names = root._x_dataStack.flatMap((layer) => Object.keys(layer));
                const data = window.Alpine.$data(root);
                const held = {};
                for (const name of names) {
                    if (typeof data[name] === 'string') held[name] = data[name];
                }
                return JSON.stringify(held);
            }"""
        )
        # A blob of nothing would let every assertion below pass on air.
        self.assertIn("exportPassphrase", blob)
        return blob

    # ---- getting to the dialog -------------------------------------------

    def _seeded_vault(self):
        """An open vault holding one entry whose password is worth hiding."""
        self._open_vault()
        self._create_entry(SEEDED_ENTRY_NAME, SEEDED_ENTRY_LOGIN, SEEDED_ENTRY_PASSWORD)

    def _generate_passphrase(self):
        """Draw a passphrase from the panel and apply it, returning the value.

        The default path, and the only one Export accepts without an
        acknowledgement: the panel drew the value, so its strength is a number
        the dialog knows rather than one it would have to invent.
        """
        box = self.page.locator(self.EXPORT_BOX)
        box.get_by_role("button", name="Passphrase").click()
        preview = box.locator(".font-mono.break-all")
        preview.wait_for(timeout=15000)
        drawn = preview.inner_text()
        self.assertTrue(drawn, "the panel opened without drawing anything")
        box.get_by_role("button", name="Use").click()
        # The field, not the panel: the walk's next step is a click on Export,
        # and what enables it is the value having landed here.
        self.page.wait_for_function(
            "() => (document.getElementById('export-passphrase') || {}).value",
            timeout=10000,
        )
        return drawn

    def _type_a_passphrase(self, phrase):
        """The other path: a phrase a human chose, confirmed and acknowledged.

        The confirmation field is instantiated by the first keystroke, so it
        cannot be filled before the passphrase is.
        """
        self.page.fill("#export-passphrase", phrase)
        self.page.wait_for_selector("#export-confirm", state="attached", timeout=10000)
        self.page.fill("#export-confirm", phrase)
        self.page.check("[data-testid='export-own-phrase-ack']")

    def _run_archive_export(self):
        self._open_export()
        self._type_a_passphrase(KNOWN_PASSPHRASE)
        with self.page.expect_download(timeout=120000) as download:
            self.page.click("[data-testid='export-run']")
        return download.value

    # ---- the walks --------------------------------------------------------

    def test_the_generator_opens_at_a_strength_worth_an_archive(self):
        """The dialog's opening draw, measured on screen.

        An archive has no secret key behind it: the passphrase is the whole of
        what protects the file, and the file can be attacked offline for as
        long as it exists. The panel's own default draw is six words - a good
        password, not a good archive key - so this dialog pins its own, and
        the pinning lives in the template. No unit test reaches that: the JS
        suite can compose the panel with the mixin's options, but only a
        browser says whether the include actually hands them over.
        """
        self._seeded_vault()
        self._open_export()
        box = self.page.locator(self.EXPORT_BOX)

        preview = box.locator(".font-mono.break-all")
        preview.wait_for(timeout=15000)
        drawn = preview.inner_text()
        self.assertEqual(
            len(drawn.split("-")), 8, f"the dialog opened on {drawn!r}, not eight words"
        )

        reported = box.get_by_text(re.compile(r"^\d+ bits")).inner_text()
        bits = int(reported.split()[0])
        self.assertGreaterEqual(
            bits, 72, f"the dialog opens at {bits} bits, under what an archive needs"
        )

    def test_no_request_carries_a_decrypted_value(self):
        """The one property here worth asserting mechanically: nothing
        plaintext may leave the page.

        The generated path, deliberately - it is the default, and the only one
        the Export button enables without an acknowledgement. A walk that
        types a phrase and forgets the checkbox clicks a disabled button and
        blames the feature.
        """
        # The whole request, not its body. A body-only watch appends "" for
        # every GET, so a leak in a query string, in a header, or through
        # `new Image().src` sails past the one assertion meant to stop it.
        # Headers are worth the noise here: they are as reachable from page
        # code as a body is, and the scan only ever looks for two known
        # strings, so nothing else in them is read.
        requests = []
        self.page.on(
            "request",
            lambda request: requests.append(
                "\n".join(
                    [request.url]
                    + [f"{name}: {value}" for name, value in request.headers.items()]
                    + [request.post_data or ""]
                )
            ),
        )
        self._seeded_vault()
        self._open_export()
        drawn = self._generate_passphrase()

        with self.page.expect_download(timeout=120000) as download:
            self.page.click("[data-testid='export-run']")
        self.assertTrue(download.value.suggested_filename.endswith(".vaultarchive"))

        self._drain_events()

        # Both secrets: the entry's password, which the export decrypts, and
        # the passphrase that seals it. Either one on the wire is the same bug.
        #
        # assertFalse rather than assertNotIn, so a failure names the request
        # instead of printing the headers that were scanned - the scan reads
        # the session cookie, and nothing is served by copying it into a log.
        for request in requests:
            line = request.splitlines()[0]
            for secret, what in (
                (SEEDED_ENTRY_PASSWORD, "an entry's password"),
                (drawn, "the export passphrase"),
            ):
                self.assertFalse(
                    secret in request, f"{what} left the page towards {line}"
                )

    def test_no_plaintext_survives_in_the_page(self):
        """Once the file is handed over, nothing it was built from is left.

        Two places, because they answer two different questions. The markup
        says whether a decrypted value was ever rendered - which is what the
        tree would be. The component's own strings say whether one is still
        held, and that is the only place a passphrase can be seen at all: it
        lives in a JS string no serialisation reaches.
        """
        self._seeded_vault()
        self._run_archive_export()
        self.page.wait_for_selector(
            "#export-passphrase", state="detached", timeout=15000
        )

        html = self.page.content()
        self.assertNotIn(SEEDED_ENTRY_PASSWORD, html)
        self.assertNotIn(KNOWN_PASSPHRASE, html)

        held = self._component_strings()
        self.assertNotIn(KNOWN_PASSPHRASE, held)
        self.assertNotIn(SEEDED_ENTRY_PASSWORD, held)

    def test_locking_takes_the_dialog_and_the_phrase_away(self):
        """The dialog goes, and so does what was typed into it.

        Re-opening after the next unlock is the half that matters. The dialog
        is mounted under x-if, so a teardown alone removes the field whether
        or not anything was cleared - and the state behind it would then paint
        the phrase straight back into the new one.
        """
        self._seeded_vault()
        self._open_export()
        self.page.fill("#export-passphrase", KNOWN_PASSPHRASE)
        self.page.evaluate("() => window.vaultSession.lock()")
        self.page.wait_for_selector("#export-passphrase", state="detached")
        self.assertNotIn(KNOWN_PASSPHRASE, self.page.content())

        self._unlock()
        self.page.wait_for_selector("text=All entries", timeout=30000)
        self._open_export()
        self.assertEqual(self.page.input_value("#export-passphrase"), "")

    def test_a_typed_passphrase_needs_its_confirmation_and_its_acknowledgement(self):
        """A phrase nothing here can measure is accepted on two conditions.

        Asserted as the button's own state at each step, because that is what
        a user meets: a walk that only clicks at the end cannot tell "the
        acknowledgement is required" from "the click did nothing".
        """
        self._seeded_vault()
        self._open_export()
        run = self.page.locator("[data-testid='export-run']")

        self.page.fill("#export-passphrase", KNOWN_PASSPHRASE)
        self.page.wait_for_selector("#export-confirm", state="attached", timeout=10000)
        self.assertTrue(run.is_disabled(), "a typed phrase alone enabled Export")

        self.page.fill("#export-confirm", KNOWN_PASSPHRASE)
        self.assertTrue(
            run.is_disabled(), "a confirmed phrase enabled Export without the tick"
        )

        self.page.check("[data-testid='export-own-phrase-ack']")
        self.page.wait_for_selector(
            "[data-testid='export-run']:not([disabled])", timeout=10000
        )
        with self.page.expect_download(timeout=120000) as download:
            run.click()
        self.assertTrue(download.value.suggested_filename.endswith(".vaultarchive"))

    def test_the_interchange_export_warns_before_it_builds_anything(self):
        """The plaintext format asks first, and cancelling builds nothing.

        This walk also settles which lookup the guard uses. In a browser the
        bare name resolves and window.AppDialog does not, so a guard written
        the window way would refuse here and no dialog would ever open.
        """
        self._seeded_vault()
        self.assertEqual(self.page.evaluate("() => typeof AppDialog"), "object")
        self.assertEqual(
            self.page.evaluate("() => typeof window.AppDialog"),
            "undefined",
            "window.AppDialog resolving would make this walk prove nothing",
        )

        self._open_export()
        self.page.check("input[value='interchange']")
        self.page.click("[data-testid='export-run']")
        self.page.wait_for_selector(f"{CONFIRM}[open]", timeout=15000)

        # Asserted while the warning is up, which is what makes "before" a
        # claim and not a coincidence: the progress line shows for as long as
        # the tree is being read, so a confirm moved after that read would
        # find it on screen behind itself. Its <p> is in the DOM either way -
        # x-show hides it - so this reads visibility, not presence.
        self.assertFalse(
            self.page.locator(
                f"{self.EXPORT_BOX} p:has-text('entries read')"
            ).is_visible(),
            "entries were being read while the warning was still on screen",
        )

        self.page.click(CONFIRM_CANCEL)
        self.page.wait_for_selector(f"{CONFIRM}[open]", state="detached", timeout=10000)
        self._drain_events()
        self.assertEqual(self._downloads, [])

    def test_a_warning_that_cannot_be_shown_refuses_the_export(self):
        """No confirm dialog, no file - never a silent yes.

        The screen's general-purpose confirm wrapper answers true when
        dialogs.js has not loaded, which is the right default for a
        destructive action the user already asked for. It is the wrong one
        here, because this confirm *is* the warning that a file holding every
        password in the clear is about to be written.
        """
        self.page.route(
            DIALOGS_SCRIPT,
            lambda route: route.fulfill(
                status=200, content_type="application/javascript", body=""
            ),
        )
        self._seeded_vault()
        self.assertEqual(self.page.evaluate("() => typeof AppDialog"), "undefined")

        self._open_export()
        self.page.check("input[value='interchange']")
        self.page.click("[data-testid='export-run']")
        self.page.wait_for_selector("text=could not be confirmed", timeout=15000)
        self._drain_events()
        self.assertEqual(self._downloads, [])
        self.assertEqual(self.page.locator(f"{CONFIRM}[open]").count(), 0)
