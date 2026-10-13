"""E2E: an open message is one browser history entry.

Opening a message from the list adds an entry, so back returns to the list
and forward reopens it; closing it steps back instead of stacking the list
twice. Landing on the page, a deep link included, adds nothing and keeps the
message in the address.
"""

from __future__ import annotations

from django.utils import timezone
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.mail.models import (
    MailAccount,
    MailFolder,
    MailLabel,
    MailMessage,
    MailMessageLabel,
)

PHONE = {"width": 390, "height": 844}
SUBJECT = "Quarterly report"


class MailHistoryTests(PlaywrightTestCase):
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
        self.inbox = MailFolder.objects.create(
            account=account, name="INBOX", display_name="Inbox", folder_type="inbox"
        )
        self.message = MailMessage.objects.create(
            account=account,
            folder=self.inbox,
            imap_uid=1,
            subject=SUBJECT,
            from_email="bob@example.com",
            date=timezone.now(),
            is_read=True,
        )
        self.label = MailLabel.objects.create(account=account, name="Finance")
        MailMessageLabel.objects.create(message=self.message, label=self.label)
        self.base = f"{self.live_server_url}/mail"
        self.login_as(self.user)

    def _goto(self, query=""):
        self.page.goto(f"{self.base}{query}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def _row(self):
        return self.page.locator("[draggable='true']", has_text=SUBJECT)

    def _detail(self):
        return self.page.locator("h2", has_text=SUBJECT)

    def _back_arrow(self):
        return self.page.get_by_role("button", name="Back to messages")

    def test_back_arrow_on_a_deep_linked_message_lands_on_its_list(self):
        self.page.set_viewport_size(PHONE)
        self._goto(f"?message={self.message.uuid}")
        expect(self._detail()).to_be_visible()

        self._back_arrow().click()

        expect(self.page).to_have_url(f"{self.base}?folder={self.inbox.uuid}")
        expect(self._row()).to_be_visible()

    def test_open_close_forward_on_a_phone_adds_one_entry(self):
        self.page.set_viewport_size(PHONE)
        self._goto()
        self._row().click()
        expect(self._detail()).to_be_visible()
        expect(self.page).to_have_url(f"{self.base}?message={self.message.uuid}")

        self._back_arrow().click()
        expect(self.page).to_have_url(self.base)
        expect(self._row()).to_be_visible()

        self.page.go_forward()
        expect(self.page).to_have_url(f"{self.base}?message={self.message.uuid}")
        expect(self._detail()).to_be_visible()

        self.page.go_back()
        expect(self._row()).to_be_visible()
        self.page.go_back()
        expect(self.page).to_have_url("about:blank")

    def test_landing_on_a_phone_adds_no_entry(self):
        self.page.set_viewport_size(PHONE)
        self._goto()
        expect(self._row()).to_be_visible()

        self.page.go_back()

        expect(self.page).to_have_url("about:blank")

    def test_deep_link_to_a_folder_and_message_keeps_the_message(self):
        self._goto(f"?folder={self.inbox.uuid}&message={self.message.uuid}")
        expect(self._detail()).to_be_visible()

        expect(self.page).to_have_url(
            f"{self.base}?folder={self.inbox.uuid}&message={self.message.uuid}"
        )

    def test_deep_link_to_a_label_and_message_keeps_both(self):
        self._goto(f"?label={self.label.uuid}&message={self.message.uuid}")
        expect(self._detail()).to_be_visible()

        expect(self.page).to_have_url(
            f"{self.base}?label={self.label.uuid}&message={self.message.uuid}"
        )

    def test_back_on_a_desktop_closes_the_opened_message(self):
        self._goto()
        self._row().click()
        expect(self._detail()).to_be_visible()

        self.page.go_back()

        expect(self.page).to_have_url(self.base)
        expect(self._detail()).to_be_hidden()

    def test_back_after_a_reload_rebuilds_without_reloading(self):
        self._goto()
        self._row().click()
        expect(self.page).to_have_url(f"{self.base}?message={self.message.uuid}")
        self.page.reload()
        expect(self._detail()).to_be_visible()
        self.page.evaluate("window.__sameDocument = true")

        self.page.go_back()

        expect(self.page).to_have_url(self.base)
        expect(self._detail()).to_be_hidden()
        self.assertTrue(self.page.evaluate("window.__sameDocument === true"))
