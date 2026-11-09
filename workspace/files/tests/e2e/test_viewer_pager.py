"""E2E: below md, previous and next leave the file for the viewer header.

The arrows floating over the file covered what it shows on a phone (the
first lines of a text, a book's page), and a swipe never reaches a viewer
that is a frame of its own. Above md they float as before. The public share
page does the same with its own header.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from django.core.files.base import ContentFile

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.common.tests.pdf_fixtures import make_pdf
from workspace.files.models import FileShareLink
from workspace.files.services import FileService

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}


class ViewerPagerTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="pager")
        self.folder = FileService.create_folder(owner=self.user, name="Notes")
        self.files = [
            FileService.create_file(
                owner=self.user,
                parent=self.folder,
                name=name,
                content=ContentFile(f"{name} body\n".encode(), name=name),
            )
            for name in ("a.txt", "b.txt", "c.txt")
        ]

    def _open_middle(self, viewport):
        self.page.set_viewport_size(viewport)
        self.login_as(self.user)
        middle = self.files[1]
        self.page.goto(
            f"{self.live_server_url}/files/{self.folder.uuid}?open={middle.uuid}"
        )
        viewer = self.page.locator("[data-file-viewer-inline]")
        viewer.locator("[data-viewer-pager] span").wait_for(state="visible")
        return viewer

    def test_on_a_phone_the_header_pages_and_nothing_floats_over_the_file(self):
        viewer = self._open_middle(PHONE)

        for title in ("Previous file", "Next file"):
            self.assertFalse(
                viewer.locator(f"button.btn-circle[title='{title}']").is_visible()
            )
        prev_button = viewer.locator("[data-viewer-pager-prev]")
        next_button = viewer.locator("[data-viewer-pager-next]")
        self.assertTrue(prev_button.is_visible())
        self.assertTrue(next_button.is_visible())
        # The header shows before the file does: the panel's wrapper stays
        # hidden, and has no box, until the content has loaded.
        panel = viewer.locator("div:has(> #viewer-panel)")
        panel.wait_for(state="visible")
        panel_top = panel.bounding_box()["y"]
        self.assertLess(next_button.bounding_box()["y"], panel_top)

        next_button.click()
        self.page.wait_for_function(
            "() => document.querySelector('[data-file-viewer-inline] h3')"
            ".textContent === 'c.txt'"
        )
        self.assertTrue(next_button.is_disabled())
        prev_button.click()
        self.page.wait_for_function(
            "() => document.querySelector('[data-file-viewer-inline] h3')"
            ".textContent === 'b.txt'"
        )

    def test_on_a_phone_the_pager_overlaps_no_header_button(self):
        # PDFs: the header then carries every button it can (slideshow,
        # favorite, new tab, download, properties, close), which leaves the
        # title column its narrowest.
        pdfs = [
            FileService.create_file(
                owner=self.user,
                parent=self.folder,
                name=name,
                content=ContentFile(make_pdf([name]), name=name),
            )
            for name in ("x.pdf", "y.pdf")
        ]
        self.page.set_viewport_size(PHONE)
        self.login_as(self.user)
        self.page.goto(
            f"{self.live_server_url}/files/{self.folder.uuid}?open={pdfs[1].uuid}"
        )
        viewer = self.page.locator("[data-file-viewer-inline]")
        viewer.locator("[data-viewer-new-tab-link]").wait_for(state="visible")
        viewer.locator("[data-viewer-pager-next]").wait_for(state="visible")

        geometry = self.page.evaluate(
            """() => {
                const viewer = document.querySelector('[data-file-viewer-inline]');
                const pager = viewer.querySelector('[data-viewer-pager]');
                const header = pager.parentElement.parentElement;
                const boxes = [...pager.children].map(c => c.getBoundingClientRect());
                const buttons = [...header.querySelectorAll('button, a')]
                    .filter(el => !pager.contains(el) && el.offsetParent)
                    .map(el => el.getBoundingClientRect());
                // Half a pixel of slack: boxes that only touch do not overlap.
                const overlaps = (a, b) => a.left < b.right - 0.5 && b.left < a.right - 0.5
                    && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
                const head = header.getBoundingClientRect();
                return {
                    overlapping: boxes.some(a => buttons.some(b => overlaps(a, b))),
                    outside: boxes.some(a => a.right > head.right + 0.5 || a.left < head.left - 0.5),
                };
            }"""
        )
        self.assertEqual(geometry, {"overlapping": False, "outside": False})

    def test_on_a_desktop_the_arrows_float_and_the_header_only_counts(self):
        viewer = self._open_middle(DESKTOP)

        self.assertTrue(
            viewer.locator("button.btn-circle[title='Next file']").is_visible()
        )
        self.assertFalse(viewer.locator("[data-viewer-pager-next]").is_visible())
        self.assertEqual(
            viewer.locator("[data-viewer-pager] span").inner_text(), "2 / 3"
        )

    def test_the_shared_page_pages_from_its_header_on_a_phone(self):
        link = FileShareLink.objects.create(
            file=self.folder, created_by=self.user, mode=FileShareLink.Mode.READ
        )
        self.page.set_viewport_size(PHONE)
        self.page.goto(
            f"{self.live_server_url}/files/shared/{link.token}?node={self.files[1].uuid}"
        )
        header_next = self.page.locator("[data-shared-pager-next]")
        header_next.wait_for(state="visible")

        self.assertFalse(self.page.locator("#shared-next").is_visible())
        header_next.click()
        self.page.wait_for_function(
            "() => document.querySelector('h1').textContent.trim() === 'c.txt'"
        )
