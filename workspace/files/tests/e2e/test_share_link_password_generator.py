"""E2E: a share link created with a generated password stores that password.

The JS tests drive the host and the panel as plain objects; what only a
browser shows is that the template wires them together - the panel mounts
under the form, its events bubble to the handlers on the wrapper, and the
value in the field after Use is the one the create request carries.
"""

from __future__ import annotations

from django.contrib.auth.hashers import check_password
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File, FileShareLink


class ShareLinkPasswordGeneratorTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="gen-owner")
        self.doc = File.objects.create(
            owner=self.user,
            name="report.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        self.login_as(self.user)

    def _open_link_form(self):
        self.page.goto(f"{self.live_server_url}/files")
        expect(
            self.page.locator(f'[data-uuid="{self.doc.uuid}"]').first
        ).to_be_visible()
        self.page.evaluate(
            """([uuid, name]) => window.dispatchEvent(new CustomEvent(
                'open-share-modal', { detail: { uuid, name, nodeType: 'file' } }
            ))""",
            [str(self.doc.uuid), self.doc.name],
        )
        dialog = self.page.locator("dialog[open]")
        dialog.get_by_role("button", name="Create link").click()
        return dialog

    def test_the_generated_password_is_the_one_stored(self):
        dialog = self._open_link_form()
        field = dialog.locator("#share-link-password")
        expect(field).to_have_attribute("type", "password")

        dialog.get_by_role("button", name="Generate a password").click()
        dialog.get_by_role("button", name="Use").click()

        # Use folds the panel and shows the value: the sender has to pass it on.
        expect(dialog.get_by_role("button", name="Regenerate")).to_have_count(0)
        expect(field).to_have_attribute("type", "text")
        generated = field.input_value()
        self.assertGreaterEqual(len(generated), 8)

        with self.page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/share-links")
        ) as created:
            dialog.get_by_role("button", name="Create", exact=True).click()
        self.assertTrue(created.value.ok)

        link = FileShareLink.objects.get(file=self.doc)
        self.assertTrue(check_password(generated, link.password))
        expect(dialog.locator("#share-link-password")).to_have_count(0)

        stored = self.page.evaluate("JSON.stringify(Object.entries(localStorage))")
        self.assertNotIn(generated, stored)

    def test_a_redraw_after_use_follows_into_the_field(self):
        dialog = self._open_link_form()
        field = dialog.locator("#share-link-password")

        dialog.get_by_role("button", name="Generate a password").click()
        dialog.get_by_role("button", name="Use").click()
        first = field.input_value()

        dialog.get_by_role("button", name="Generate a password").click()
        dialog.get_by_role("button", name="Regenerate").click()
        expect(field).not_to_have_value(first)
        self.assertTrue(field.input_value())

    def test_opening_the_panel_scrolls_it_into_the_modal(self):
        # 720px is the height where the panel's buttons open below the modal's
        # fold: the modal is capped at the viewport and scrolls its own body.
        self.page.set_viewport_size({"width": 1280, "height": 720})
        dialog = self._open_link_form()
        dialog.get_by_role("button", name="Generate a password").click()
        use = dialog.get_by_role("button", name="Use")
        expect(use).to_be_visible()

        self.page.wait_for_function(
            """(button) => {
                const box = button.closest('.modal-box').getBoundingClientRect();
                const rect = button.getBoundingClientRect();
                return rect.top >= box.top && rect.bottom <= box.bottom;
            }""",
            arg=use.element_handle(),
            timeout=2000,
        )
