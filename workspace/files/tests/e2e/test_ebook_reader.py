"""E2E: an EPUB opens in the e-book reader, and the book's scripts never run.

foliate-js renders each chapter in a blob: iframe whose sandbox cannot hold
(it needs allow-scripts and allow-same-origin together), so the reader page's
Content-Security-Policy is all that stands between a book's JavaScript and the
reader's session. The security test plants every way a chapter can carry code
and checks none of it ran, while the chapter itself still shows.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from django.core.files.base import ContentFile

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import FileShareLink
from workspace.files.services import FileService
from workspace.files.tests.ebooks import (
    cbz_bytes,
    epub_bytes,
    fb2_bytes,
    fbz_bytes,
    mobi_bytes,
)

# Each payload marks the top page, which the chapter frame could reach were
# its script allowed to run: the frames are all same-origin.
_MARK = "window.top.document.documentElement.dataset.pwned"
_SCRIPTED_CHAPTER = f"""
<p>A chapter that carries scripts.</p>
<script>{_MARK} = 'inline';</script>
<script src="payload.js"></script>
<img src="missing.png" alt="" onerror="{_MARK} = 'handler'"/>
"""


class EbookReaderTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="reader")

    def _open(self, data, name="book.epub"):
        file_obj = FileService.create_file(
            owner=self.user, name=name, content=ContentFile(data, name=name)
        )
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/files?open={file_obj.uuid}")
        reader = self.page.frame_locator("#viewer-panel iframe[data-ebook-reader]")
        reader.locator("body[data-ready]").wait_for(state="attached", timeout=30_000)
        return reader

    def _chapter_text(self):
        """Text of the chapter frames foliate created (inside a closed shadow root)."""
        texts = []
        for frame in self.page.frames:
            if frame.url.startswith("blob:"):
                texts.append(frame.evaluate("() => document.body?.innerText || ''"))
        return "\n".join(texts)

    def test_an_epub_opens_on_its_first_chapter_and_turns_pages(self):
        reader = self._open(epub_bytes(title="Nineteen"))

        self.assertEqual(reader.locator("[data-ref=title]").inner_text(), "Nineteen")
        self.assertFalse(reader.locator("[data-ref=loading]").is_visible())
        self.assertFalse(reader.locator("[data-ref=error]").is_visible())
        self.assertFalse(reader.locator("[data-ref=toc]").is_visible())
        self.page.wait_for_function(
            "() => [...document.querySelectorAll('iframe')].length > 0"
        )
        self.assertIn("bright cold day", self._chapter_text())

        reader.locator("[data-action=toc]").click()
        reader.get_by_role("button", name="Chapter Two").click()
        self.page.wait_for_function(
            """() => {
                const frame = document.querySelector('#viewer-panel iframe[data-ebook-reader]');
                const chapter = frame.contentDocument.querySelector('[data-ref=chapter]');
                return chapter && chapter.textContent.trim() === 'Chapter Two';
            }""",
            timeout=10_000,
        )
        self.assertIn("striking thirteen", self._chapter_text())

    def test_every_text_format_opens_on_its_first_chapter(self):
        for name, data in [
            ("book.mobi", mobi_bytes(title="Nineteen")),
            ("book.azw3", mobi_bytes(title="Nineteen")),
            ("book.fb2", fb2_bytes(title="Nineteen")),
            ("book.fbz", fbz_bytes(title="Nineteen")),
        ]:
            with self.subTest(name=name):
                reader = self._open(data, name=name)
                self.assertEqual(
                    reader.locator("[data-ref=title]").inner_text(), "Nineteen"
                )
                self.page.wait_for_function(
                    "() => [...document.querySelectorAll('iframe')].length > 0"
                )
                self.assertIn("bright cold day", self._chapter_text())
                self.assertTrue(reader.locator("[data-action=larger]").is_visible())

    def test_a_comic_book_shows_its_first_page(self):
        reader = self._open(cbz_bytes(pages=3), name="comic.cbz")
        self.assertFalse(reader.locator("[data-action=larger]").is_visible())

        def page_drawn():
            for frame in self.page.frames:
                if frame.url.startswith("blob:") and frame.evaluate(
                    "() => { const img = document.querySelector('img');"
                    " return !!img && img.complete && img.naturalWidth === 60; }"
                ):
                    return True
            return False

        for _ in range(50):
            if page_drawn():
                break
            self.page.wait_for_timeout(100)
        self.assertTrue(page_drawn())

    def test_a_book_that_cannot_be_read_says_so(self):
        file_obj = FileService.create_file(
            owner=self.user,
            name="broken.epub",
            content=ContentFile(b"PK\x03\x04 not a book", name="broken.epub"),
        )
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/files?open={file_obj.uuid}")
        reader = self.page.frame_locator("#viewer-panel iframe[data-ebook-reader]")

        error = reader.locator("[data-ref=error]")
        error.wait_for(state="visible", timeout=30_000)
        self.assertIn("could not be opened", error.inner_text())
        self.assertFalse(reader.locator("[data-ref=loading]").is_visible())

    def test_a_book_cannot_load_a_script_the_site_serves(self):
        # A same-origin URL serving JavaScript: a file anyone can publish
        # behind a public link. A policy allowing the site's own scripts by
        # origin would run it in the chapter, with the reader's session.
        script = FileService.create_file(
            owner=self.user,
            name="payload.js",
            content=ContentFile(
                f"{_MARK} = 'site-script';".encode(), name="payload.js"
            ),
        )
        link = FileShareLink.objects.create(
            file=script, created_by=self.user, mode=FileShareLink.Mode.READ
        )
        src = f"{self.live_server_url}/api/v1/files/shared/{link.token}/content"
        self._open(
            epub_bytes(
                chapters=[
                    ("Scripted", f'<p>Loads a script.</p><script src="{src}"></script>')
                ]
            )
        )
        self.page.wait_for_function(
            "() => [...document.querySelectorAll('iframe')].length > 0"
        )
        self.assertIn("Loads a script", self._chapter_text())
        self.page.wait_for_timeout(500)

        marks = [
            frame.evaluate("() => document.documentElement.dataset.pwned || null")
            for frame in self.page.frames
        ]
        self.assertEqual([mark for mark in marks if mark], [])

    def test_the_scripts_a_book_carries_never_run(self):
        data = epub_bytes(
            chapters=[("Scripted", _SCRIPTED_CHAPTER)],
            resources={
                "payload.js": ("application/javascript", f"{_MARK} = 'file';"),
            },
        )
        self._open(data)
        self.page.wait_for_function(
            "() => [...document.querySelectorAll('iframe')].length > 0"
        )
        self.assertIn("carries scripts", self._chapter_text())
        # Give a script that was let through every chance to have run.
        self.page.wait_for_timeout(500)

        marks = [
            frame.evaluate("() => document.documentElement.dataset.pwned || null")
            for frame in self.page.frames
        ]
        self.assertEqual([mark for mark in marks if mark], [])
