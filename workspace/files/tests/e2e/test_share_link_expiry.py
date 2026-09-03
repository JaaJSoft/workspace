"""E2E: a share link expiring mid-session must not leave a dead page.

alpine-ajax removes its swap target outright when a fragment request comes
back as a 200 with no matching element in the body - which is exactly what
``shared_file_view`` returns once a link has expired, since ``show_content``
is false and ``{% block content %}`` never renders. A Django test can only
see that the reload handler's markup is present, not whether Alpine ever
binds it - the first attempt at this fix rendered a handler that Alpine
silently ignored because its wrapper carried no ``x-data`` (no component
scope, no listener). Only a real browser proves the click actually reloads
into the expired card instead of leaving the page a bare footer.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File, FileShareLink


class ShareLinkExpiryTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="expiry-owner")
        self.root = File.objects.create(
            owner=self.owner, name="Docs", node_type=File.NodeType.FOLDER
        )
        self.sub = File.objects.create(
            owner=self.owner,
            name="Sub",
            node_type=File.NodeType.FOLDER,
            parent=self.root,
        )
        self.link = FileShareLink.objects.create(
            file=self.root,
            created_by=self.owner,
            mode=FileShareLink.Mode.READ,
        )

    def test_expiring_mid_session_reloads_into_the_expired_card(self):
        # The visitor's browser is never logged in - the share page is the
        # anonymous path, and login_as would defeat the point of the test.
        self.page.goto(f"{self.live_server_url}/files/shared/{self.link.token}")
        expect(self.page.get_by_text("Sub")).to_be_visible()

        # The link expires while the visitor is still looking at the page -
        # nothing in the open tab changes yet, only the next request will
        # see it.
        self.link.expires_at = timezone.now() - timedelta(days=1)
        self.link.save(update_fields=["expires_at"])

        self.page.get_by_text("Sub").click()

        expect(self.page.get_by_text("Link expired")).to_be_visible()
        # The failure this pins down: alpine-ajax tore the target out and
        # left everything below the header gone, with no card at all.
        self.assertNotIn("Sub", self.page.content())
