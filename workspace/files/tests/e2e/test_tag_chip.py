"""E2E: <tag-chip> renders the pill in a real browser.

The chip is a custom element, so nothing about it is verifiable from
Django's test client — the server sends `<tag-chip name="..." ...>` and
the browser turns it into the pill. Two things are pinned here:

1. The element upgrades and paints the tag's own color. Before the chip,
   colors were daisyUI tokens interpolated into `badge-<token>`, which
   silently produced nothing for the picker's `pink-500`/`orange-500`.
2. It upgrades during parsing, not after: the script is loaded in
   `<head>` without `defer` precisely so server-rendered chips never
   flash unstyled.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File, FileTag, Tag


class TagChipRenderingTests(PlaywrightTestCase):
    def test_chip_renders_the_tag_color(self):
        user = self.create_user(username="chip-user")
        self.login_as(user)

        file = File.objects.create(
            owner=user,
            name="invoice.txt",
            node_type=File.NodeType.FILE,
        )
        tag = Tag.objects.create(owner=user, name="urgent", color="#ef4444")
        FileTag.objects.create(file=file, tag=tag)

        self.page.goto(f"{self.live_server_url}/files")
        # The properties panel is where the browser renders a file's tags;
        # dispatch the event its row click handler dispatches.
        self.page.evaluate(
            "(uuid) => window.dispatchEvent(new CustomEvent('open-properties', "
            "{ detail: { uuid, nodeType: 'file' } }))",
            str(file.uuid),
        )

        chip = self.page.locator("tag-chip", has_text="urgent").first
        expect(chip).to_be_visible()

        # Upgraded: the element applied the pill geometry to itself.
        expect(chip).to_have_class(re.compile(r"\brounded-full\b"))

        border = chip.evaluate("el => getComputedStyle(el).borderTopColor")
        text = chip.evaluate("el => getComputedStyle(el).color")
        self.assertEqual(border, "rgb(239, 68, 68)")
        self.assertEqual(text, "rgb(239, 68, 68)")

    def test_chip_is_defined_before_the_document_finishes_parsing(self):
        """The head script is what keeps server-rendered chips from
        flashing unstyled; a deferred one would upgrade too late."""
        user = self.create_user(username="chip-timing")
        self.login_as(user)

        self.page.goto(f"{self.live_server_url}/files")
        defined_at_parse = self.page.evaluate(
            "() => !!customElements.get('tag-chip')",
        )
        self.assertTrue(defined_at_parse)

        script_is_deferred = self.page.evaluate(
            "() => Array.from(document.head.querySelectorAll('script[src]'))"
            ".some(s => s.src.includes('tag_chip') && (s.defer || s.async))",
        )
        self.assertFalse(script_is_deferred)
