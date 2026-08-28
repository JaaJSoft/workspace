"""Tests for the chat UI `conversation_list_view` partial endpoint.

Covers the HTML partial returned for the chat sidebar (Alpine AJAX refresh),
including the `?q=` search filter.
"""

import re
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    PinnedConversation,
)
from workspace.chat.ui.views import _build_conversation_context
from workspace.common.tests.rows import count_rows

from .test_chat import ChatTestMixin

User = get_user_model()


class ConversationListViewPartialTests(ChatTestMixin, TestCase):
    URL = "/chat/conversations"

    def test_unauthenticated_redirects_to_login(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 302)

    def test_returns_html_partial_with_conversation_list_root(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="conversation-list"')
        self.assertContains(resp, "Test Group")

    def test_search_filters_group_conversation_by_title(self):
        other_group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Project Phoenix",
            created_by=self.creator,
        )
        ConversationMember.objects.create(conversation=other_group, user=self.creator)

        self.client.force_login(self.creator)
        resp = self.client.get(self.URL, {"q": "phoenix"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Project Phoenix")
        self.assertNotContains(resp, "Test Group")

    def test_search_filters_dm_by_other_member_name(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.URL, {"q": "member"})
        self.assertEqual(resp.status_code, 200)
        # DM with member should be visible (display_name = "member")
        self.assertContains(resp, 'id="conversation-list"')
        # Group should be filtered out (title is "Test Group", no "member")
        self.assertNotContains(resp, "Test Group")

    def test_search_is_case_insensitive(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.URL, {"q": "TEST GROUP"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Test Group")

    def test_blank_search_returns_all_conversations(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.URL, {"q": "   "})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Test Group")

    def test_dm_and_group_appear_in_single_merged_list(self):
        """DM and Group are rendered together, sorted by updated_at desc."""
        # Group older, DM more recent.
        now = timezone.now()
        Conversation.objects.filter(pk=self.group.pk).update(
            updated_at=now - timedelta(hours=2)
        )
        Conversation.objects.filter(pk=self.dm.pk).update(
            updated_at=now - timedelta(hours=1)
        )

        self.client.force_login(self.creator)
        resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()

        # Both should be in the merged list.
        self.assertIn(str(self.group.uuid), body)
        self.assertIn(str(self.dm.uuid), body)

        # DM (more recent) should appear before the group in document order.
        dm_pos = body.find(str(self.dm.uuid))
        group_pos = body.find(str(self.group.uuid))
        self.assertNotEqual(dm_pos, -1, "DM uuid should appear in the rendered HTML")
        self.assertNotEqual(
            group_pos, -1, "Group uuid should appear in the rendered HTML"
        )
        self.assertLess(
            dm_pos,
            group_pos,
            "More recently updated DM should be rendered before the older group",
        )

    def test_no_section_headers_for_dm_or_group(self):
        """The DMs/Groups section headers no longer exist; Pinned header still does."""
        # Pin the group so the Pinned section renders
        PinnedConversation.objects.create(
            owner=self.creator,
            conversation=self.group,
            position=0,
        )

        self.client.force_login(self.creator)
        resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        # Old section headers must be gone
        self.assertNotContains(resp, "Direct Messages")
        self.assertNotContains(
            resp, ">Groups<"
        )  # avoid matching avatar group containers
        # Pinned header must remain
        self.assertContains(resp, "Pinned")

    def test_pinned_section_remains_separate(self):
        """A pinned conversation is rendered before non-pinned ones, regardless of updated_at."""
        # Group is pinned but older; DM is unpinned but more recent.
        now = timezone.now()
        Conversation.objects.filter(pk=self.group.pk).update(
            updated_at=now - timedelta(days=10)
        )
        Conversation.objects.filter(pk=self.dm.pk).update(updated_at=now)
        PinnedConversation.objects.create(
            owner=self.creator,
            conversation=self.group,
            position=0,
        )

        self.client.force_login(self.creator)
        resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()

        group_pos = body.find(str(self.group.uuid))
        dm_pos = body.find(str(self.dm.uuid))
        self.assertNotEqual(
            group_pos, -1, "Group uuid should appear in the rendered HTML"
        )
        self.assertNotEqual(dm_pos, -1, "DM uuid should appear in the rendered HTML")
        self.assertLess(
            group_pos,
            dm_pos,
            "Pinned group must come before the more recent unpinned DM",
        )

    def test_time_ago_month_label_uses_active_timezone(self):
        msg = Message.objects.create(
            conversation=self.group, author=self.member, body="hello"
        )
        # 23:30 UTC on Jan 31 is already Feb 1 in Paris.
        Message.objects.filter(pk=msg.pk).update(
            created_at=datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
        )
        timezone.activate("Europe/Paris")
        self.addCleanup(timezone.deactivate)
        # Freeze "now" so the message stays in the month-label branch
        # (< 1 year old) regardless of when the test runs.
        fixed_now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        with patch("django.utils.timezone.now", return_value=fixed_now):
            convs = _build_conversation_context(self.creator)
        group = next(c for c in convs if c.pk == self.group.pk)
        self.assertEqual(group.time_ago, "Feb 01")


class ConversationAvatarMarkupTests(ChatTestMixin, TestCase):
    """The sidebar row and the API payload must label a conversation alike.

    They used to derive the avatar separately - the server rendering initials
    into the row, the client recomputing them from the member list for the
    header - and had drifted apart.
    """

    TAG = re.compile(r"<conversation-avatar\b[^>]*>")
    # Bare flags carry no value, so they are absent from the pairs below.
    FLAGS = ("has-avatar", "presence")

    def _row_avatar(self, html, uuid):
        """The attributes of the sidebar row's <conversation-avatar>."""
        for tag in self.TAG.findall(html):
            attrs = dict(re.findall(r'\s([a-z-]+)="([^"]*)"', tag))
            if attrs.get("uuid") != str(uuid):
                continue
            attrs.update(
                {flag: "" for flag in self.FLAGS if re.search(rf"\s{flag}[\s>]", tag)}
            )
            return attrs
        raise AssertionError(
            f"no <conversation-avatar> for {uuid} in the rendered sidebar"
        )

    def test_sidebar_and_api_agree_on_every_conversation(self):
        self.client.force_login(self.creator)
        html = self.client.get("/chat/conversations").content.decode()
        payload = self.client.get("/api/v1/chat/conversations").json()

        self.assertTrue(payload)
        for conv in payload:
            attrs = self._row_avatar(html, conv["uuid"])
            self.assertEqual(attrs["kind"], conv["kind"])
            self.assertEqual(attrs["initials"], conv["avatar_initial"])

    def test_a_dm_row_carries_the_other_participant(self):
        self.client.force_login(self.creator)
        html = self.client.get("/chat/conversations").content.decode()

        attrs = self._row_avatar(html, self.dm.uuid)
        self.assertEqual(attrs["user-id"], str(self.member.id))
        self.assertEqual(attrs["username"], self.member.username)
        self.assertEqual(attrs["initials"], "M")

    def test_a_group_row_carries_its_members_initials(self):
        self.client.force_login(self.creator)
        html = self.client.get("/chat/conversations").content.decode()

        attrs = self._row_avatar(html, self.group.uuid)
        self.assertNotIn("user-id", attrs)
        self.assertEqual(attrs["initials"], "M")

    def test_the_uploaded_picture_is_flagged_on_the_row(self):
        self.client.force_login(self.creator)
        html = self.client.get("/chat/conversations").content.decode()
        self.assertNotIn("has-avatar", self._row_avatar(html, self.group.uuid))

        Conversation.objects.filter(pk=self.group.pk).update(has_avatar=True)
        html = self.client.get("/chat/conversations").content.decode()
        self.assertIn("has-avatar", self._row_avatar(html, self.group.uuid))

    def test_a_crowded_group_row_and_the_api_pick_the_same_members(self):
        """The row reads a three-member window, the API the whole list.

        They only stay in step while both walk the members in the same order,
        which is what an untitled group of more than three exposes.
        """
        crowd = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            created_by=self.creator,
        )
        ConversationMember.objects.create(conversation=crowd, user=self.creator)
        for i in range(6):
            user = User.objects.create_user(
                username=f"crowd-{i}", password="pass", first_name=f"Crowd{i}"
            )
            ConversationMember.objects.create(conversation=crowd, user=user)

        self.client.force_login(self.creator)
        html = self.client.get("/chat/conversations").content.decode()
        payload = self.client.get("/api/v1/chat/conversations").json()

        api = next(c for c in payload if c["uuid"] == str(crowd.uuid))
        self.assertEqual(self._row_avatar(html, crowd.uuid)["initials"], "CC")
        self.assertEqual(
            self._row_avatar(html, crowd.uuid)["initials"], api["avatar_initial"]
        )
        self.assertIn("Crowd0, Crowd1, Crowd2", html)


class ConversationListRowVolumeTests(ChatTestMixin, TestCase):
    """The sidebar refresh renders names, never the member list itself.

    Every row is labelled from at most three other members, so growing a group
    must not grow what the endpoint reads. The query count stays flat either
    way, so only a row count pins this down.
    """

    URL = "/chat/conversations"

    def _add_members(self, count, prefix):
        for i in range(count):
            user = User.objects.create_user(username=f"{prefix}{i}", password="pass")
            ConversationMember.objects.create(conversation=self.group, user=user)

    def test_row_volume_does_not_scale_with_members_per_conversation(self):
        self.client.force_login(self.creator)
        self._add_members(5, "seed-")

        with count_rows(ConversationMember) as baseline:
            self.client.get(self.URL)

        self._add_members(50, "bulk-")
        with count_rows(ConversationMember) as after:
            resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            after.count,
            baseline.count,
            msg=(
                "ConversationMember rows must not scale with members per "
                f"conversation - baseline={baseline.count}, "
                f"after adding 50 members={after.count}"
            ),
        )
