"""End-to-end tests for the chat sidebar's alpine-ajax refresh.

Two bug classes:
  1. The morph of ``#conversation-list`` on search drops the
     ``@contextmenu`` Alpine listener on the surviving ``<li>``.
  2. The morph accidentally re-initializes the parent ``x-data`` scope,
     resetting the ``collapsed`` sidebar state.

Skipped unless ``E2E=1`` is set.
"""
from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember
from workspace.common.tests.e2e.base import PlaywrightTestCase


def re_w16():
    """Regex matching the Tailwind class for a collapsed sidebar."""
    return re.compile(r"\bw-16\b")


class ChatSidebarRefreshTests(PlaywrightTestCase):
    """Locks down the alpine-ajax morph of #conversation-list."""

    def setUp(self):
        super().setUp()
        # Main user — the one who sees the sidebar.
        self.user = self.create_user(username="viewer", password="pass12345")
        # Two DM peers. DM display name falls back to the peer's username
        # when get_full_name() is empty, so the search query matches them
        # directly.
        self.peer_a = self.create_user(username="alice-contact")
        self.peer_b = self.create_user(username="bob-contact")

        # Two DM conversations, each with the viewer + one peer as active
        # members.
        self.conv_a = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user,
        )
        self.conv_b = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user,
        )
        for conv, peer in [(self.conv_a, self.peer_a), (self.conv_b, self.peer_b)]:
            ConversationMember.objects.create(conversation=conv, user=self.user)
            ConversationMember.objects.create(conversation=conv, user=peer)

    def test_search_filter_morphs_and_preserves_context_menu(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")

        # Both DM conversations are in the initial DOM. We target <li>
        # inside #conversation-list: the template uses <li class="list-none
        # group/conv"> without data-conversation-uuid on DM items, so we
        # match by text content.
        list_root = self.page.locator("#conversation-list")
        expect(list_root.get_by_text("alice-contact")).to_be_visible()
        expect(list_root.get_by_text("bob-contact")).to_be_visible()

        # Type in the search box — triggers a 300 ms debounced $ajax swap.
        self.page.locator('input[placeholder="Search conversations"]').fill(
            "alice-contact"
        )

        # After the morph, alice-contact's DM survives; bob-contact's is
        # gone. Playwright auto-waits on to_be_visible / to_have_count.
        expect(list_root.get_by_text("alice-contact")).to_be_visible()
        expect(list_root.get_by_text("bob-contact")).to_have_count(0)

        # Right-click the surviving <li>. If the @contextmenu listener
        # survived the morph, Alpine flips `ctxMenu.open = true` and the
        # menu partial (gated by x-show) becomes visible. Its first item
        # text is "Conversation info".
        surviving = list_root.locator("li", has_text="alice-contact").first
        surviving.click(button="right")
        expect(
            self.page.get_by_text("Conversation info", exact=True).first
        ).to_be_visible()

    def test_refresh_preserves_parent_state_and_reorders(self):
        from datetime import timedelta

        from django.utils import timezone

        # Give the two conversations distinct updated_at. `auto_now=True`
        # on the field means we MUST bypass .save() — use .update().
        now = timezone.now()
        Conversation.objects.filter(pk=self.conv_a.pk).update(updated_at=now)
        Conversation.objects.filter(pk=self.conv_b.pk).update(
            updated_at=now - timedelta(minutes=10),
        )

        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")

        # DM items rendered in ``#conversation-list`` — there's no
        # data-conversation-uuid on DM <li>, so we match by text content.
        # conv_a is the most recent → alice-contact's <li> is first.
        list_root = self.page.locator("#conversation-list")
        expect(list_root.get_by_text("alice-contact")).to_be_visible()
        expect(list_root.get_by_text("bob-contact")).to_be_visible()

        def first_dm_name():
            """Return the display_name of the first DM <li> in the list.

            Works whether the sidebar is expanded (reads innerText) or
            collapsed (reads the button's title attribute, which the
            template sets to the display_name when collapsed=true).
            """
            return self.page.evaluate(
                "() => {"
                "  const list = document.querySelector('#conversation-list');"
                "  const first = list && list.querySelector('li.list-none.group\\\\/conv');"
                "  if (!first) return null;"
                "  const btn = first.querySelector('button[title]');"
                "  if (btn && btn.title) return btn.title;"
                "  return first.innerText.trim();"
                "}"
            )

        # Baseline: alice-contact first.
        assert "alice-contact" in (first_dm_name() or ""), (
            f"expected alice-contact first, got: {first_dm_name()!r}"
        )

        # Collapse the sidebar. Button's accessible name is "Collapse
        # sidebar" while expanded.
        self.page.get_by_role("button", name="Collapse sidebar").click()
        aside = self.page.locator("aside").first
        expect(aside).to_have_class(re_w16())

        # Server-side reorder: bump conv_b above conv_a.
        Conversation.objects.filter(pk=self.conv_b.pk).update(
            updated_at=timezone.now(),
        )

        # Trigger the alpine-ajax refresh via the nested x-data that owns
        # refreshList. We pick the element via its x-data attribute
        # substring — the only scope that contains "convSearch" in the
        # whole chat page.
        # We use Alpine.evaluate() rather than Alpine.$data().refreshList()
        # because $ajax is an Alpine magic that is only available in the
        # component's evaluation context — not on the plain data proxy.
        self.page.evaluate(
            "() => Alpine.evaluate("
            "document.querySelector('[x-data*=\"convSearch\"]'), "
            "'refreshList()'"
            ")"
        )

        # Wait until the first DM <li> is bob-contact — proves the morph
        # landed and the server's -updated_at ordering is reflected
        # client-side. When the sidebar is collapsed the name is only
        # available via the button's title attribute (the template binds
        # title=display_name when collapsed), so we check both title and
        # innerText to be robust regardless of sidebar state.
        self.page.wait_for_function(
            "() => {"
            "  const list = document.querySelector('#conversation-list');"
            "  const first = list && list.querySelector('li.list-none.group\\\\/conv');"
            "  if (!first) return false;"
            "  const btn = first.querySelector('button[title]');"
            "  const name = (btn && btn.title) || first.innerText;"
            "  return name.includes('bob-contact');"
            "}"
        )

        # Parent Alpine state preserved: sidebar still collapsed.
        expect(aside).to_have_class(re_w16())

        # And alice-contact is now second, not gone.
        second_name = self.page.evaluate(
            "() => {"
            "  const list = document.querySelector('#conversation-list');"
            "  const items = list && list.querySelectorAll('li.list-none.group\\\\/conv');"
            "  if (!items || items.length < 2) return null;"
            "  const btn = items[1].querySelector('button[title]');"
            "  return (btn && btn.title) || items[1].innerText.trim();"
            "}"
        )
        assert "alice-contact" in (second_name or ""), (
            f"expected alice-contact second, got: {second_name!r}"
        )
