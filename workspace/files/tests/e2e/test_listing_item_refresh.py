"""E2E: a processed file re-renders its own item, not the whole listing.

A ``files.preview_ready`` or ``files.processing_settled`` push names one
file. The page asks the listing for that node alone and alpine-ajax swaps
its row or card in place, so the other items are the same elements as
before and what the table painted on them (selection, hidden columns) is
still there. The swapped item is a new element: the table has to adopt it,
or the next sort would put the stale one back. When the swap cannot cover
the push, the whole listing is re-rendered as before.

The pushes are dispatched by hand here, as the SSE client does on receipt.
"""

from __future__ import annotations

from django.core.cache import cache
from django.utils import timezone
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File
from workspace.users.services.settings import set_setting

ITEMS = "#folder-browser [data-uuid]"
SPINNER = "[title^='Processing:']"


class ListingItemRefreshTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="owner")
        self.alpha, self.beta, self.gamma = (
            File.objects.create(
                owner=self.owner,
                name=name,
                node_type=File.NodeType.FILE,
                mime_type="text/plain",
                processing_status=File.ProcessingStatus.PENDING,
            )
            for name in ("alpha.txt", "beta.txt", "gamma.txt")
        )
        self.login_as(self.owner)
        self.listing_requests = []
        self.page.on("request", self._record)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _record(self, request):
        if request.headers.get("x-alpine-request"):
            self.listing_requests.append(request.url)

    def _open(self, mode="list", items=3):
        set_setting(self.owner, "files", "preferences", {"defaultViewMode": mode})
        with self.page.expect_response(
            lambda r: r.request.method == "POST" and "/api/v1/files/actions" in r.url
        ):
            self.page.goto(f"{self.live_server_url}/files")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        expect(self.page.locator(ITEMS)).to_have_count(items)
        # A mark on every element: a re-rendered item is a new one without it.
        self.page.locator(ITEMS).evaluate_all(
            "(items) => items.forEach((item) => { item.keptSinceLoad = true; })"
        )

    def _item(self, node):
        return self.page.locator(f"#folder-browser [data-uuid='{node.uuid}']")

    def _kept(self, node):
        return self._item(node).evaluate("(item) => item.keptSinceLoad === true")

    def _push(self, event_type, *nodes):
        self.page.evaluate(
            "([type, uuids]) => uuids.forEach((uuid) => window.dispatchEvent("
            "new CustomEvent('sse:files.' + type, { detail: { file_uuid: uuid } })))",
            [event_type, [str(node.uuid) for node in nodes]],
        )

    def _settle(self, *nodes):
        File.objects.filter(pk__in=[node.pk for node in nodes]).update(
            processing_status=File.ProcessingStatus.READY
        )
        self._push("processing_settled", *nodes)

    def _select(self, node):
        self._item(node).locator("input[data-select-item]").click(force=True)
        expect(self._item(node)).to_have_attribute("data-selected", "")

    def test_one_card_is_swapped_and_the_others_are_left_alone(self):
        self._open("mosaic")
        self._select(self.beta)

        File.objects.filter(pk=self.gamma.pk).update(has_thumbnail=True)
        self._push("preview_ready", self.gamma)

        expect(self._item(self.gamma).locator("img[src*='/thumbnail']")).to_have_count(
            1
        )
        self.assertFalse(self._kept(self.gamma))
        self.assertTrue(self._kept(self.alpha))
        self.assertTrue(self._kept(self.beta))
        expect(self._item(self.beta)).to_have_attribute("data-selected", "")
        expect(self._item(self.beta).locator("input[data-select-item]")).to_be_checked()
        expect(self.page.locator("#folder-browser")).to_contain_text("1 selected")
        # One request, for that node alone.
        self.assertEqual(len(self.listing_requests), 1)
        self.assertIn(f"nodes={self.gamma.uuid}", self.listing_requests[0])

    def test_a_burst_of_pushes_is_one_request(self):
        self._open()

        self._settle(self.alpha, self.gamma)

        expect(self._item(self.alpha).locator(SPINNER)).to_have_count(0)
        expect(self._item(self.gamma).locator(SPINNER)).to_have_count(0)
        expect(self._item(self.beta).locator(SPINNER)).to_have_count(1)
        self.assertTrue(self._kept(self.beta))
        self.assertEqual(len(self.listing_requests), 1)

    def test_a_swapped_item_keeps_its_selection(self):
        self._open("mosaic")
        self._select(self.gamma)

        self._settle(self.gamma)

        expect(self._item(self.gamma).locator(SPINNER)).to_have_count(0)
        self.assertFalse(self._kept(self.gamma))
        expect(self._item(self.gamma)).to_have_attribute("data-selected", "")
        expect(
            self._item(self.gamma).locator("input[data-select-item]")
        ).to_be_checked()
        # The new element answers the controls: unticking it clears the bar.
        self._item(self.gamma).locator("input[data-select-item]").click(force=True)
        expect(self._item(self.gamma)).not_to_have_attribute("data-selected", "")

    def test_a_swapped_row_takes_the_table_layout(self):
        set_setting(
            self.owner,
            "files",
            "table_controls",
            {
                "columnVisibility": {"size": False},
                "columnOrder": [
                    "select",
                    "icon",
                    "name",
                    "modified",
                    "favorite",
                    "tags",
                    "size",
                    "created",
                    "actions",
                ],
            },
        )
        self._open()

        def layout(node):
            return self._item(node).evaluate(
                "(row) => Array.from(row.children).map("
                "(cell) => cell.dataset.col + ':' + cell.style.display)"
            )

        untouched = layout(self.alpha)
        self.assertIn("size:none", untouched)

        self._settle(self.gamma)

        expect(self._item(self.gamma).locator(SPINNER)).to_have_count(0)
        self.assertEqual(layout(self.gamma), untouched)

    def test_a_sort_after_the_swap_keeps_the_new_row(self):
        self._open()
        self._settle(self.gamma)
        expect(self._item(self.gamma).locator(SPINNER)).to_have_count(0)

        browser = self.page.locator("#folder-browser")
        browser.get_by_title("Sort").click()
        sheet = browser.get_by_role("dialog", name="Sort")
        sheet.get_by_role("radio", name="Name").click()
        sheet.get_by_role("radio", name="Name").click()

        names = self.page.locator("#folder-browser tbody tr[data-uuid]").evaluate_all(
            "(rows) => rows.map((row) => row.dataset.displayName)"
        )
        self.assertEqual(names, ["gamma.txt", "beta.txt", "alpha.txt"])
        expect(self._item(self.gamma).locator(SPINNER)).to_have_count(0)
        expect(self.page.locator(f"{ITEMS} {SPINNER}")).to_have_count(2)

    def test_a_file_of_another_listing_asks_for_nothing(self):
        elsewhere = File.objects.create(
            owner=self.owner, name="Elsewhere", node_type=File.NodeType.FOLDER
        )
        inside = File.objects.create(
            owner=self.owner,
            name="inside.txt",
            node_type=File.NodeType.FILE,
            parent=elsewhere,
        )
        self._open(items=4)

        self._push("processing_settled", inside)
        # Past the grouping delay of the pushes.
        self.page.wait_for_timeout(700)

        self.assertEqual(self.listing_requests, [])

    def test_a_row_filtered_out_re_renders_the_listing(self):
        self._open()
        self.page.locator("#folder-browser").get_by_placeholder("Filter by name").fill(
            "alpha"
        )
        expect(self.page.locator("#folder-browser tbody tr[data-uuid]")).to_have_count(
            1
        )

        self._settle(self.gamma)

        expect(self.page.locator(f"{ITEMS} {SPINNER}")).to_have_count(2)
        self.assertFalse(self._kept(self.alpha))
        self.assertNotIn("nodes=", self.listing_requests[-1])

    def test_an_item_gone_from_the_listing_re_renders_it(self):
        self._open()

        File.objects.filter(pk=self.gamma.pk).update(deleted_at=timezone.now())
        self._settle(self.alpha, self.gamma)

        expect(self._item(self.gamma)).to_have_count(0)
        expect(self._item(self.alpha).locator(SPINNER)).to_have_count(0)
        expect(self.page.locator("#folder-browser")).to_contain_text("2 items")
        self.assertIn("nodes=", self.listing_requests[0])
        self.assertNotIn("nodes=", self.listing_requests[-1])
