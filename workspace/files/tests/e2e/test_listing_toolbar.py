"""E2E: the listing toolbar holds on one line at every width.

The bar used to wrap onto two or three lines as soon as the window narrowed,
the browser zoomed in or the properties panel opened: the search box kept a
fixed width and every control carried its label. It now collapses labels to
icons against the width of the listing itself (a container query), moves the
filters and the sort into popovers, which turn into bottom sheets on a phone,
and lists the filters in force as removable chips.

Only a browser can tell: the toolbar is server-rendered markup whose layout
is decided by CSS, so these tests measure the real geometry.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File, FileTag, Tag

BAR = "[data-testid='listing-filter-bar']"
ROWS = "#folder-browser tbody tr[data-uuid]"


class ListingToolbarTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="toolbar")
        File.objects.create(
            owner=self.user, name="Archive", node_type=File.NodeType.FOLDER
        )
        work = Tag.objects.create(owner=self.user, name="Work", color="#3b82f6")
        urgent = Tag.objects.create(owner=self.user, name="Urgent", color="#ef4444")
        for name, tags in (
            ("Quarterly budget 2026 - final version.xlsx", [work]),
            ("Contract draft.pdf", [urgent]),
            ("notes.md", []),
        ):
            node = File.objects.create(
                owner=self.user, name=name, node_type=File.NodeType.FILE
            )
            for tag in tags:
                FileTag.objects.create(file=node, tag=tag)
        self.login_as(self.user)

    def _open(self, width, height=900):
        self.page.set_viewport_size({"width": width, "height": height})
        self.page.goto(f"{self.live_server_url}/files")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        expect(self.page.locator(ROWS)).to_have_count(4)
        # The clipboard paste button only shows with something cut or
        # copied; force it so the widest bar is the one measured.
        self.page.evaluate(
            "() => { const d = Alpine.$data(document.querySelector("
            "'#folder-browser [x-data^=fileTableWithView]'));"
            " d.hasClipboardItems = true; }"
        )

    def _assert_single_line(self, label):
        geometry = self.page.locator(BAR).evaluate(
            """bar => {
                const tops = Array.from(bar.children)
                    .filter(el => el.offsetParent !== null)
                    .map(el => Math.round(el.getBoundingClientRect().top));
                return {
                    tops,
                    height: bar.getBoundingClientRect().height,
                    overflow: bar.scrollWidth - bar.clientWidth,
                };
            }"""
        )
        self.assertLessEqual(geometry["height"], 34, f"{label}: {geometry}")
        spread = max(geometry["tops"]) - min(geometry["tops"])
        self.assertLessEqual(spread, 2, f"{label}: {geometry}")
        self.assertLessEqual(geometry["overflow"], 0, f"{label}: {geometry}")

    def test_the_bar_stays_on_one_line_at_every_width(self):
        for width in (1280, 1024, 820, 640, 390, 320):
            self._open(width)
            self._assert_single_line(f"{width}px")

    def test_the_bar_stays_on_one_line_next_to_the_properties_panel(self):
        self._open(1024)
        uuid = self.page.locator(ROWS).first.get_attribute("data-uuid")
        self.page.evaluate(
            "(uuid) => window.dispatchEvent(new CustomEvent('open-properties', "
            "{ detail: { uuid, nodeType: 'file' } }))",
            uuid,
        )
        expect(self.page.locator("#properties-sidebar")).to_be_visible()
        self.page.wait_for_function(
            "() => document.getElementById('properties-sidebar')"
            ".getBoundingClientRect().width >= 319"
        )
        self._assert_single_line("1024px + properties panel")

    def test_filters_narrow_the_listing_and_show_as_removable_chips(self):
        self._open(1280)
        browser = self.page.locator("#folder-browser")
        browser.get_by_title("Filters").click()
        sheet = browser.get_by_role("dialog", name="Filters")
        sheet.get_by_role("button", name="Files", exact=True).click()
        sheet.locator("label", has_text="Work").click()
        self.page.keyboard.press("Escape")
        expect(sheet).to_be_hidden()

        expect(self.page.locator(ROWS)).to_have_count(1)
        expect(browser.get_by_title("Filters")).to_contain_text("2")
        chips = browser.locator("[data-testid='active-filters'] tag-chip")
        expect(chips).to_have_count(2)
        expect(chips.filter(has_text="Files")).to_be_visible()

        chips.filter(has_text="Work").get_by_role("button", name="Remove Work").click()
        expect(self.page.locator(ROWS)).to_have_count(3)
        browser.get_by_role("button", name="Clear all").click()
        expect(self.page.locator(ROWS)).to_have_count(4)
        expect(browser.get_by_role("button", name="Clear all")).to_be_hidden()

    def test_popovers_open_as_bottom_sheets_on_a_phone(self):
        self._open(390, 844)
        self.page.locator("#folder-browser").get_by_title("Sort").click()
        sheet = self.page.locator("#folder-browser").get_by_role("dialog", name="Sort")
        expect(sheet).to_be_visible()
        self.page.wait_for_timeout(250)  # let the enter transition settle
        box = sheet.bounding_box()
        self.assertEqual(round(box["x"]), 0)
        self.assertEqual(round(box["width"]), 390)
        self.assertEqual(round(box["y"] + box["height"]), 844)

        sheet.get_by_role("radio", name="Name").click()
        sheet.get_by_role("radio", name="Name").click()
        names = self.page.locator(ROWS).evaluate_all(
            "rows => rows.map(r => r.dataset.displayName)"
        )
        self.assertEqual(
            names,
            [
                "Quarterly budget 2026 - final version.xlsx",
                "notes.md",
                "Contract draft.pdf",
                "Archive",
            ],
        )
        sheet.get_by_role("button", name="Close").click()
        expect(sheet).to_be_hidden()
