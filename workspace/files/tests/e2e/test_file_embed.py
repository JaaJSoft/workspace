"""E2E tests for files embedded in a note (files/ui/js/file_embed.js).

The editor draws an embed from what POST /api/v1/files/embeds answers for
the person reading the note: the file inline by type, or a placeholder that
shows nothing of it.
"""

from __future__ import annotations

import io
import json
import time

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from PIL import Image
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File, FileShare
from workspace.files.services import FileService

from ._share_fixtures import build_shared_tree

User = get_user_model()


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 60, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def _embed(f):
    return f"![{f.name}](/api/v1/files/{f.uuid}/content)"


class RealDragMixin:
    """A real HTML5 drag, and what both ends of it negotiated.

    Playwright's synthesized drags report ``effectAllowed = 'all'``, so only
    the values the page itself wrote are worth asserting: the source's
    ``effectAllowed`` at dragstart and the target's ``dropEffect`` at
    dragover, read after their handlers ran (bubble phase on window).
    """

    def record_negotiation(self):
        self.page.evaluate(
            """(mime) => {
                window.__drag = { effectAllowed: null, dropEffect: null, refs: null };
                window.addEventListener('dragstart', (e) => {
                    window.__drag.effectAllowed = e.dataTransfer.effectAllowed;
                    window.__drag.refs = e.dataTransfer.getData(mime) || null;
                });
                window.addEventListener('dragover', (e) => {
                    if (e.target.closest && e.target.closest('.ProseMirror')) {
                        window.__drag.dropEffect = e.dataTransfer.dropEffect;
                    }
                });
            }""",
            "application/x-workspace-file-refs",
        )
        return lambda: self.page.evaluate("window.__drag")

    def drag(self, source, x, y):
        src = source.bounding_box()
        sx, sy = src["x"] + src["width"] / 2, src["y"] + src["height"] / 2
        self.page.mouse.move(sx, sy)
        self.page.mouse.down()
        # A short first move inside the source is what makes Chromium promote
        # the gesture to a drag and emit dragstart.
        self.page.mouse.move(sx, sy + 8, steps=5)
        self.page.mouse.move(x, y, steps=10)
        self.page.mouse.up()


class FileEmbedTests(RealDragMixin, PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="embedder")
        self.photo = FileService.create_file(
            self.owner,
            "photo.png",
            content=ContentFile(_png_bytes(), name="photo.png"),
            mime_type="image/png",
        )
        self.pdf = FileService.create_file(
            self.owner,
            "report.pdf",
            content=ContentFile(b"%PDF-1.4\n%%EOF\n", name="report.pdf"),
            mime_type="application/pdf",
        )
        self.login_as(self.owner)

    def _note(self, body, name="Embeds.md"):
        return FileService.create_file(
            self.owner,
            name,
            content=ContentFile(body.encode(), name=name),
            mime_type="text/markdown",
        )

    def _open(self, note):
        # The notes app auto-opens the note named by ?file=, mounting the viewer.
        self.page.goto(f"{self.live_server_url}/notes?file={note.uuid}")
        editor = self.page.locator(".ProseMirror").first
        expect(editor).to_be_visible(timeout=20000)
        return editor

    def _embed_of(self, f):
        return self.page.locator(f'.file-embed[data-file-embed="{f.uuid}"]')

    def _saved_markdown(self, note):
        note.refresh_from_db()
        with note.content.open("rb") as fh:
            return fh.read().decode()

    def _save(self, note, *fragments):
        """Save with Ctrl+S and return the stored markdown once it holds every
        fragment. The autosave may have written it first: Ctrl+S on a saved
        buffer sends nothing, so the store is what is waited on."""
        self.page.keyboard.press("Control+s")
        deadline = time.monotonic() + 20
        while True:
            saved = self._saved_markdown(note)
            if all(fragment in saved for fragment in fragments):
                return saved
            if time.monotonic() > deadline:
                self.fail(f"never saved {fragments!r}; stored: {saved!r}")
            self.page.wait_for_timeout(200)

    def test_image_and_pdf_render_inline(self):
        note = self._note(f"# Report\n\n{_embed(self.photo)}\n\n{_embed(self.pdf)}\n")
        self._open(note)

        image = self._embed_of(self.photo)
        expect(image).to_have_attribute("data-embed-kind", "image", timeout=10000)
        img = image.locator("img")
        expect(img).to_be_visible()
        self.page.wait_for_function(
            "(el) => el.complete && el.naturalWidth === 64", arg=img.element_handle()
        )
        expect(image).to_contain_text("photo.png")

        pdf = self._embed_of(self.pdf)
        expect(pdf).to_have_attribute("data-embed-kind", "pdf", timeout=10000)
        expect(pdf.locator("iframe")).to_have_attribute(
            "src",
            f"/api/v1/files/{self.pdf.uuid}/content?v={self.pdf.content_hash[:16]}",
        )

    def test_embed_survives_an_edit_round_trip(self):
        body = f"# Report\n\n{_embed(self.photo)}\n"
        note = self._note(body)
        editor = self._open(note)
        expect(self._embed_of(self.photo)).to_have_attribute(
            "data-embed-status", "ok", timeout=10000
        )

        editor.locator("h1").click()
        self.page.keyboard.press("End")
        self.page.keyboard.type(" v2")
        saved = self._save(note, "# Report v2")
        self.assertIn(_embed(self.photo), saved)

    def test_renamed_source_shows_its_new_name(self):
        note = self._note(f"{_embed(self.pdf)}\n")
        FileService.rename(self.pdf, "final.pdf")
        self._open(note)
        expect(self._embed_of(self.pdf)).to_contain_text("final.pdf", timeout=10000)

    def test_reader_without_access_sees_a_placeholder(self):
        note = self._note(f"{_embed(self.photo)}\n")
        reader = self.create_user(username="reader")
        FileShare.objects.create(file=note, shared_by=self.owner, shared_with=reader)
        content_requests = []
        self.page.on(
            "request",
            lambda r: (
                content_requests.append(r.url)
                if str(self.photo.uuid) in r.url and "/embeds" not in r.url
                else None
            ),
        )
        self.login_as(reader)
        # A share recipient opens the note from "Shared with me".
        self.page.goto(f"{self.live_server_url}/files?shared=1&open={note.uuid}")

        embed = self._embed_of(self.photo)
        expect(embed).to_have_attribute(
            "data-embed-status", "unavailable", timeout=20000
        )
        expect(embed).to_contain_text("you don't have access")
        self.assertEqual(embed.locator("img, iframe, video, audio").count(), 0)
        self.assertEqual(content_requests, [])

    def test_trashed_target_renders_a_fallback(self):
        note = self._note(f"{_embed(self.pdf)}\n")
        self.pdf.soft_delete()
        self._open(note)
        embed = self._embed_of(self.pdf)
        expect(embed).to_have_attribute("data-embed-status", "trashed", timeout=10000)
        expect(embed).to_contain_text("in the trash")

    def test_purged_target_renders_a_fallback(self):
        note = self._note(f"{_embed(self.pdf)}\n")
        target = self.pdf.uuid
        self.pdf.delete(hard=True)
        self._open(note)
        embed = self.page.locator(f'.file-embed[data-file-embed="{target}"]')
        expect(embed).to_have_attribute(
            "data-embed-status", "unavailable", timeout=10000
        )
        expect(embed).to_contain_text("report.pdf")

    def test_bang_double_bracket_embeds_a_file(self):
        body = "# Notes\n\nStart here.\n"
        note = self._note(body)
        editor = self._open(note)
        editor.locator("p").first.click()
        self.page.keyboard.press("End")
        self.page.keyboard.press("Enter")
        with self.page.expect_response(
            lambda r: "/api/v1/files" in r.url and "search=report" in r.url,
            timeout=20000,
        ):
            self.page.keyboard.type("![[report")
        item = self.page.locator('[data-testid="wikilink-item"]', has_text="report.pdf")
        expect(item).to_be_visible(timeout=5000)
        item.click()

        expect(self._embed_of(self.pdf)).to_have_attribute(
            "data-embed-kind", "pdf", timeout=10000
        )
        expect(editor).not_to_contain_text("![[report")
        self._save(note, _embed(self.pdf))

    def test_dropped_file_references_are_embedded_in_order(self):
        body = "# Notes\n\nFirst paragraph.\n\nSecond paragraph.\n"
        note = self._note(body)
        editor = self._open(note)
        target = editor.locator("p", has_text="First paragraph.")
        box = target.bounding_box()
        accepted = self.page.evaluate(
            """([refs, x, y]) => {
                const target = document.elementFromPoint(x, y);
                const dt = new DataTransfer();
                window.fileRefsDrag.write(dt, refs);
                const init = { bubbles: true, cancelable: true, clientX: x, clientY: y, dataTransfer: dt };
                const over = new DragEvent('dragover', init);
                target.dispatchEvent(over);
                target.dispatchEvent(new DragEvent('drop', init));
                return over.defaultPrevented;
            }""",
            [
                [
                    {"uuid": str(self.photo.uuid), "name": "photo.png"},
                    {"uuid": str(self.pdf.uuid), "name": "report.pdf"},
                ],
                box["x"] + box["width"] - 2,
                box["y"] + box["height"] / 2,
            ],
        )
        self.assertTrue(accepted)

        expect(self._embed_of(self.photo)).to_have_attribute(
            "data-embed-kind", "image", timeout=10000
        )
        expect(self._embed_of(self.pdf)).to_have_attribute("data-embed-kind", "pdf")
        saved = self._save(note, _embed(self.photo), _embed(self.pdf))
        first = saved.index("First paragraph.")
        self.assertLess(first, saved.index(_embed(self.photo)))
        self.assertLess(saved.index(_embed(self.photo)), saved.index(_embed(self.pdf)))
        self.assertLess(saved.index(_embed(self.pdf)), saved.index("Second paragraph."))

    def test_note_dragged_from_the_list_is_embedded(self):
        folder = FileService.create_folder(self.owner, "Notes")
        host = self._note("# Host\n\nDrop below.\n", name="Host.md")
        FileService.move(host, folder)
        dragged = self._note("# Dragged\n", name="Dragged.md")
        FileService.move(dragged, folder)
        editor = self._open(host)
        row = self.page.locator(f'[data-note-uuid="{dragged.uuid}"]')
        expect(row).to_be_visible(timeout=10000)

        negotiated = self.record_negotiation()
        box = editor.locator("p", has_text="Drop below.").bounding_box()
        self.drag(row, box["x"] + box["width"] - 2, box["y"] + box["height"] / 2)

        embed = self._embed_of(dragged)
        expect(embed).to_have_attribute("data-embed-kind", "note", timeout=10000)
        expect(embed.locator("a")).to_have_attribute(
            "href", f"/notes?file={dragged.uuid}"
        )
        result = negotiated()
        self.assertEqual(result["effectAllowed"], "copy")
        self.assertEqual(result["dropEffect"], "copy")


class FileEmbedFilesListingTests(RealDragMixin, PlaywrightTestCase):
    """A row of the Files listing carries its file as a reference too."""

    def test_movable_file_row_drags_as_a_file_reference(self):
        user = self.create_user(username="lister")
        f = FileService.create_file(
            user,
            "report.pdf",
            content=ContentFile(b"%PDF-1.4\n%%EOF\n", name="report.pdf"),
            mime_type="application/pdf",
        )
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/files")
        row = self.page.locator(f'[data-uuid="{f.uuid}"][draggable="true"]').first
        expect(row).to_be_visible(timeout=20000)

        negotiated = self.record_negotiation()
        # Dropped on nothing that takes it: only the drag's payload matters.
        self.drag(row, 5, 5)

        result = negotiated()
        # The editor asks for 'copy': it has to be a member of what the row allows.
        self.assertIn("copy", result["effectAllowed"].lower())
        self.assertEqual(
            json.loads(result["refs"]), [{"uuid": str(f.uuid), "name": "report.pdf"}]
        )
        self.assertTrue(File.objects.filter(pk=f.pk, deleted_at__isnull=True).exists())


class FileEmbedPublicLinkTests(PlaywrightTestCase):
    """A visitor of a public link reads the note, never what it embeds."""

    def test_anonymous_visitor_sees_placeholders(self):
        build_shared_tree(self)
        photo = FileService.create_file(
            self.owner,
            "private.png",
            content=ContentFile(_png_bytes(), name="private.png"),
            mime_type="image/png",
        )
        body = f"# Hello\n\n{_embed(photo)}\n".encode()
        self.readme.content = ContentFile(body, name="readme.md")
        self.readme.size = len(body)
        self.readme.save()
        asked = []
        self.page.on(
            "request",
            lambda r: (
                asked.append(r.url)
                if "/embeds" in r.url or str(photo.uuid) in r.url
                else None
            ),
        )

        self.page.goto(
            f"{self.live_server_url}/files/shared/{self.link.token}"
            f"?node={self.readme.uuid}"
        )

        embed = self.page.locator(f'.file-embed[data-file-embed="{photo.uuid}"]')
        expect(embed).to_have_attribute(
            "data-embed-status", "unavailable", timeout=20000
        )
        self.assertEqual(embed.locator("img").count(), 0)
        self.assertEqual(asked, [])
