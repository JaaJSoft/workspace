"""E2E: the imports dialogs draw their own icons, not the whole page's.

The remote folder listing is stubbed in the browser: what is under test is
how the page draws it, not the WebDAV round trip. Skipped unless E2E=1.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.common.tests.e2e.icons import IconScopeMixin
from workspace.imports.models import ImportConnection, ImportJob, ImportJobItem

BROWSE = {
    "kind": "files",
    "path": "/",
    "entries": [
        {
            "id": "/Docs/",
            "name": "Docs",
            "is_dir": True,
            "size": 0,
            "modified_at": None,
            "etag": "",
            "mime_type": "",
        }
    ],
}


class ImportsIconScopeTests(IconScopeMixin, PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="import-icons")
        self.conn = ImportConnection.objects.create(
            owner=self.user,
            provider="nextcloud",
            label="Cloud",
            base_url="https://cloud.example.org",
            username="alice",
        )
        self.login_as(self.user)
        self.page.route(
            "**/api/v1/imports/connections/*/browse**",
            lambda route: route.fulfill(
                content_type="application/json", body=json.dumps(BROWSE)
            ),
        )

    def _open(self):
        self.page.goto(f"{self.live_server_url}/imports")
        expect(self.page.get_by_text("Cloud").first).to_be_visible()
        # The debug toolbar overlays the page and swallows pointer events.
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.forget_bystander_redraws()

    def test_the_connection_dialog(self):
        self._open()
        self.page.get_by_role("button", name="Add connection").click()

        expect(self.page.locator("dialog[open]")).to_be_visible()
        self.assert_bystander_kept()

    def test_the_import_wizard(self):
        self._open()
        wizard = self.page.locator("dialog[open]")
        self.page.get_by_role("button", name="New import").click()
        expect(wizard).to_be_visible()
        self.assert_bystander_kept()

        wizard.get_by_role("button", name="Continue").click()
        wizard.get_by_role("checkbox").first.check()
        self.assert_bystander_kept()

        wizard.get_by_role("button", name="Continue").click()
        expect(
            wizard.get_by_role("button", name="Docs").locator(
                'svg[data-lucide="folder"]'
            )
        ).to_be_attached()
        self.assert_bystander_kept()

        wizard.get_by_role("button", name="Back").click()
        expect(wizard.get_by_text("What should be brought over?")).to_be_visible()
        self.assert_bystander_kept()

    def test_the_errors_dialog(self):
        job = ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            status="failed",
            stats={"files": {"failed": 1}},
        )
        ImportJobItem.objects.create(
            job=job,
            kind="file",
            remote_id="/broken.txt",
            status="failed",
            error="Boom",
        )
        self._open()
        self.page.get_by_role("button", name="Errors (1)").click()

        expect(self.page.locator("dialog[open]")).to_contain_text("broken.txt")
        self.assert_bystander_kept()
