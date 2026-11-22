"""E2E: a re-render of the listing keeps what the user was doing in it.

The stream script dispatches ``sse:reconnect`` each time the stream opens
again, and the file browser re-renders ``#folder-browser`` to catch up. The
swap builds the listing component again, which used to drop the selection
and the filters: files picked for a bulk action were no longer selected
(#1821). The event is dispatched by hand here, as the stream script does.
"""

from __future__ import annotations

from django.utils import timezone
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File

ITEMS = "#folder-browser [data-uuid]"


class ListingResyncTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="owner")
        self.alpha, self.beta, self.gamma = (
            File.objects.create(
                owner=self.owner,
                name=name,
                node_type=File.NodeType.FILE,
                mime_type="text/plain",
            )
            for name in ("alpha.txt", "beta.txt", "gamma.txt")
        )
        self.login_as(self.owner)

    def _open(self, path="/files", items=3):
        with self.page.expect_response(
            lambda r: r.request.method == "POST" and "/api/v1/files/actions" in r.url
        ):
            self.page.goto(f"{self.live_server_url}{path}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        expect(self.page.locator(ITEMS)).to_have_count(items)

    def _item(self, node):
        return self.page.locator(f"#folder-browser [data-uuid='{node.uuid}']")

    def _select(self, node):
        self._item(node).locator("input[data-select-item]").click(force=True)
        expect(self._item(node)).to_have_attribute("data-selected", "")

    def _reconnect(self):
        # A mark on the listing: the re-rendered one is a new element without it.
        self.page.evaluate(
            "document.querySelector('#folder-browser').renderedBefore = true"
        )
        self.page.evaluate("window.dispatchEvent(new CustomEvent('sse:reconnect'))")
        self.page.wait_for_function(
            "() => document.querySelector('#folder-browser')?.renderedBefore !== true"
        )

    def test_a_reconnect_keeps_the_selection(self):
        self._open()
        self._select(self.alpha)
        self._select(self.gamma)

        self._reconnect()

        expect(self._item(self.alpha)).to_have_attribute("data-selected", "")
        expect(self._item(self.gamma)).to_have_attribute("data-selected", "")
        expect(self._item(self.beta)).not_to_have_attribute("data-selected", "")
        expect(
            self._item(self.alpha).locator("input[data-select-item]")
        ).to_be_checked()
        expect(self.page.locator("#folder-browser")).to_contain_text("2 selected")

    def test_a_file_gone_meanwhile_drops_out_of_the_selection(self):
        self._open()
        self._select(self.alpha)
        self._select(self.gamma)
        File.objects.filter(pk=self.gamma.pk).update(deleted_at=timezone.now())

        self._reconnect()

        expect(self.page.locator(ITEMS)).to_have_count(2)
        expect(self._item(self.alpha)).to_have_attribute("data-selected", "")
        expect(self.page.locator("#folder-browser")).to_contain_text("1 selected")

    def test_a_reconnect_keeps_the_name_filter(self):
        self._open()
        search = self.page.locator("#folder-browser").get_by_placeholder(
            "Filter by name"
        )
        search.fill("alpha")
        expect(self.page.locator("#folder-browser tbody tr[data-uuid]")).to_have_count(
            1
        )

        self._reconnect()

        expect(
            self.page.locator("#folder-browser").get_by_placeholder("Filter by name")
        ).to_have_value("alpha")
        expect(self.page.locator("#folder-browser tbody tr[data-uuid]")).to_have_count(
            1
        )

    def test_another_view_starts_without_a_selection(self):
        folder = File.objects.create(
            owner=self.owner, name="Folder", node_type=File.NodeType.FOLDER
        )
        File.objects.create(
            owner=self.owner,
            name="inside.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            parent=folder,
        )
        self._open(items=4)
        self._select(self.alpha)

        self.page.evaluate(f"window.folderNav.navigateTo('/files/{folder.uuid}')")
        expect(self.page.locator(ITEMS)).to_have_count(1)
        self.page.evaluate("window.folderNav.navigateTo('/files')")

        expect(self.page.locator(ITEMS)).to_have_count(4)
        expect(self.page.locator(f"{ITEMS}[data-selected]")).to_have_count(0)
