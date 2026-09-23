"""E2E: a sharing change refreshes the open Properties panel in place.

The share modal announces its changes with a ``shares-changed`` event, and
the browser answered it by asking for the file already on show - which the
panel reads as a second click on the same file, and closes. The user saw
the panel vanish right after sharing, instead of listing the new share.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File, FileShare
from workspace.files.services.sharing import share_file


class PropertiesPanelSharesChangedTests(PlaywrightTestCase):
    def test_panel_stays_open_and_lists_the_new_share(self):
        owner = self.create_user(username="owner")
        bob = self.create_user(username="bob")
        file = File.objects.create(
            owner=owner, name="report.txt", node_type=File.NodeType.FILE
        )
        self.login_as(owner)
        self.page.goto(f"{self.live_server_url}/files")
        self.page.evaluate(
            "(uuid) => window.dispatchEvent(new CustomEvent('open-properties', "
            "{ detail: { uuid, nodeType: 'file' } }))",
            str(file.uuid),
        )
        panel = self.page.locator("#properties-sidebar")
        expect(panel.locator("#properties-content")).to_contain_text(
            "Not shared with anyone"
        )

        share_file(
            file,
            target_user=bob,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=owner,
        )
        self.page.evaluate("window.dispatchEvent(new CustomEvent('shares-changed'))")

        expect(panel).to_be_visible()
        expect(panel.locator("#properties-content")).to_contain_text("bob")
        expect(panel.locator("#properties-content")).not_to_contain_text(
            "Not shared with anyone"
        )
