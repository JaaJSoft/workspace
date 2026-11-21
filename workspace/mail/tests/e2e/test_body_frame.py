"""E2E: an email's HTML body stays inside a frame of its own.

The body may carry the app's own classes or a ``position: fixed`` style; once
rendered it must not cover, restyle or reach anything outside its frame.
Remote images wait until the user shows them, for one message or for a
sender, and links open in a new tab with no handle back on the app.
"""

from __future__ import annotations

import base64
import re

from django.core.files.base import ContentFile
from django.utils import timezone
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.mail.models import (
    MailAccount,
    MailAttachment,
    MailFolder,
    MailMessage,
    MailTrustedSender,
)

SUBJECT = "Session expired"
TRACKER = "https://tracker.example.test/pixel.png"
PHISHING = "https://phishing.example.test/login"
# The overlay the issue describes: the app's own Tailwind classes plus an
# inline style, either of which used to draw it over the whole interface. The
# paragraphs give the frame a height: an overlay alone takes no room in it.
OVERLAY_BODY = (
    "<p>Dear customer,</p><p>Your session has expired.</p><p>Regards</p>"
    '<div class="fixed inset-0 z-50 bg-base-100" '
    'style="position: fixed; inset: 0; z-index: 2147483647; background: red">'
    f'<a href="{PHISHING}">Sign in again</a>'
    f'<img src="{TRACKER}" alt="pixel">'
    "</div>"
)
EMBEDDED_SHOWN = "Pictures sent with the message itself are shown."
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class MailBodyFrameTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        account = MailAccount.objects.create(
            owner=self.user,
            email="alice@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="alice@example.com",
        )
        inbox = MailFolder.objects.create(
            account=account, name="INBOX", display_name="Inbox", folder_type="inbox"
        )
        self.account, self.inbox = account, inbox
        self.message = MailMessage.objects.create(
            account=account,
            folder=inbox,
            imap_uid=1,
            subject=SUBJECT,
            from_email="Mallory@Example.com",
            date=timezone.now(),
            is_read=True,
            body_html=OVERLAY_BODY,
        )
        self.tracker_hits = []
        self.context.route(re.compile(r"^https://tracker\.example\.test/"), self._pixel)
        self.context.route(
            re.compile(r"^https://phishing\.example\.test/"),
            lambda route: route.fulfill(status=200, body="<p>elsewhere</p>"),
        )
        self.login_as(self.user)

    def _pixel(self, route):
        self.tracker_hits.append(route.request.url)
        route.fulfill(status=200, body=PIXEL, content_type="image/png")

    def _open(self):
        self.page.goto(f"{self.live_server_url}/mail?message={self.message.uuid}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.overlay_link = self.page.frame_locator(
            "iframe.mail-body-frame"
        ).get_by_role("link", name="Sign in again")
        expect(self.overlay_link).to_be_visible()

    def _wait_for_pixel(self):
        self.page.wait_for_function(
            "() => document.querySelector('iframe.mail-body-frame')"
            ".contentDocument.querySelector('img')?.naturalWidth === 1"
        )

    def _frame_box(self):
        return self.page.locator("iframe.mail-body-frame").bounding_box()

    def test_a_fixed_overlay_stays_inside_its_frame(self):
        self.page.goto(f"{self.live_server_url}/mail?message={self.message.uuid}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        # The subject and the body come from the same response: once the
        # subject shows, the body is on the page too.
        expect(self.page.locator("h2", has_text=SUBJECT)).to_be_visible()

        # The subject above the body is still what the user sees and clicks.
        subject_on_top = self.page.evaluate(
            """(subject) => {
                const h2 = [...document.querySelectorAll('h2')]
                    .find((el) => el.textContent.includes(subject));
                const r = h2.getBoundingClientRect();
                const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                return h2.contains(hit);
            }""",
            SUBJECT,
        )
        self.assertTrue(subject_on_top)

        expect(self.page.get_by_text("Your session has expired.")).to_have_count(0)
        expect(
            self.page.frame_locator("iframe.mail-body-frame").get_by_text(
                "Your session has expired."
            )
        ).to_be_visible()
        frame = self._frame_box()
        viewport = self.page.viewport_size
        overlay = (
            self.page.frame_locator("iframe.mail-body-frame")
            .locator("div")
            .first.bounding_box()
        )
        self.assertGreaterEqual(overlay["x"], frame["x"] - 0.5)
        self.assertGreaterEqual(overlay["y"], frame["y"] - 0.5)
        self.assertLessEqual(
            overlay["x"] + overlay["width"], frame["x"] + frame["width"] + 0.5
        )
        self.assertLessEqual(
            overlay["y"] + overlay["height"], frame["y"] + frame["height"] + 0.5
        )
        # The frame is sized to the three paragraphs, not to the window.
        self.assertLess(
            frame["width"] * frame["height"],
            viewport["width"] * viewport["height"] / 2,
        )

    def test_the_apps_styles_do_not_reach_the_body(self):
        self._open()

        # bg-base-100 would resolve to the theme color if the app's
        # stylesheet applied; only the inline red is left.
        background = (
            self.page.frame_locator("iframe.mail-body-frame")
            .locator("div")
            .first.evaluate("(el) => getComputedStyle(el).backgroundColor")
        )
        self.assertEqual(background, "rgb(255, 0, 0)")

    def test_remote_images_wait_for_show_images(self):
        self._open()
        self.page.wait_for_timeout(300)
        self.assertEqual(self.tracker_hits, [])
        expect(self.page.get_by_text("Remote images hidden")).to_be_visible()
        # Nothing in this message is sent with it, so the why says nothing of it.
        self.page.get_by_role("button", name="Why are images hidden?").click()
        expect(
            self.page.get_by_text("Loading them would tell the sender")
        ).to_be_visible()
        expect(self.page.get_by_text(EMBEDDED_SHOWN)).to_be_hidden()

        self.page.get_by_role("button", name="Show images").click()

        self._wait_for_pixel()
        self.assertEqual(self.tracker_hits, [TRACKER])
        self.assertFalse(MailTrustedSender.objects.exists())

    def test_a_trusted_sender_shows_images_on_the_next_open(self):
        self._open()
        self.page.get_by_role("button", name="More actions").click()
        self.page.get_by_role("button", name="Always show from this sender").click()
        expect(
            self.page.get_by_text("Images from this sender are always shown.")
        ).to_be_visible()
        self.assertTrue(
            MailTrustedSender.objects.filter(
                owner=self.user, email="mallory@example.com"
            ).exists()
        )

        self._wait_for_pixel()

        self.tracker_hits.clear()
        self._open()

        self._wait_for_pixel()
        self.assertEqual(self.tracker_hits, [TRACKER])
        expect(self.page.get_by_role("button", name="Show images")).to_be_hidden()

    def test_a_link_opens_in_a_new_tab_without_an_opener(self):
        self._open()

        with self.context.expect_page() as popup_info:
            self.overlay_link.click()
        popup = popup_info.value
        popup.wait_for_load_state()

        self.assertEqual(popup.url, PHISHING)
        self.assertTrue(popup.evaluate("window.opener === null"))
        # The app's own tab stays where it was.
        self.assertIn(f"message={self.message.uuid}", self.page.url)

    def test_an_embedded_picture_shows_without_asking(self):
        message = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=2,
            subject="Logo inside",
            from_email="news@example.com",
            date=timezone.now(),
            is_read=True,
            body_html=(
                '<p>Our new logo:</p><img src="cid:logo@example.com" alt="Logo">'
                f'<img src="{TRACKER}" alt="pixel">'
            ),
        )
        for filename, content_id in (
            ("logo.png", "logo@example.com"),
            ("terms.png", ""),
        ):
            MailAttachment.objects.create(
                message=message,
                filename=filename,
                content_type="image/png",
                size=len(PIXEL),
                content=ContentFile(PIXEL, name=filename),
                content_id=content_id,
                is_inline=bool(content_id),
            )

        self.page.goto(f"{self.live_server_url}/mail?message={message.uuid}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

        logo = self.page.frame_locator("iframe.mail-body-frame").get_by_alt_text("Logo")
        expect(logo).to_be_visible()
        self.page.wait_for_function(
            "() => document.querySelector('iframe.mail-body-frame')"
            ".contentDocument.querySelector('img[alt=Logo]')?.naturalWidth === 1"
        )
        # The tracker next to it still waits for consent, and the notice says
        # which images it is about.
        expect(self.page.get_by_role("button", name="Show images")).to_be_visible()
        self.page.get_by_role("button", name="Why are images hidden?").click()
        expect(self.page.get_by_text(EMBEDDED_SHOWN)).to_be_visible()
        self.assertEqual(self.tracker_hits, [])
        # The picture shown in the body is not listed again as a file.
        expect(self.page.get_by_role("link", name="terms.png")).to_be_visible()
        expect(self.page.get_by_role("link", name="logo.png")).to_have_count(0)
