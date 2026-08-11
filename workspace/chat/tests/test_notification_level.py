"""Tests for the per-member conversation notification level.

Covers the write endpoint (PUT .../notification-level) and the read path
that feeds the header button its current state.
"""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import ConversationMember

from .test_chat import ChatTestMixin

User = get_user_model()

Level = ConversationMember.NotificationLevel


class NotificationLevelEndpointTests(ChatTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = f"/api/v1/chat/conversations/{self.group.uuid}/notification-level"
        self.client.force_authenticate(self.member)

    def _level(self):
        return ConversationMember.objects.get(
            conversation=self.group,
            user=self.member,
        ).notification_level

    def test_unauthenticated_returns_403(self):
        self.client.force_authenticate(None)
        resp = self.client.put(self.url, {"level": Level.NONE}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_member_cannot_set_a_level(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.put(self.url, {"level": Level.NONE}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self._level(), Level.ALL)

    def test_sets_the_level(self):
        resp = self.client.put(self.url, {"level": Level.MENTIONS}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["notification_level"], Level.MENTIONS)
        self.assertEqual(self._level(), Level.MENTIONS)

    def test_rejects_an_unknown_level(self):
        resp = self.client.put(self.url, {"level": "sometimes"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._level(), Level.ALL)

    def test_rejects_a_missing_level(self):
        resp = self.client.put(self.url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_only_touches_the_caller_membership(self):
        self.client.put(self.url, {"level": Level.NONE}, format="json")

        others = ConversationMember.objects.filter(
            conversation=self.group,
        ).exclude(user=self.member)
        for member in others:
            self.assertEqual(member.notification_level, Level.ALL)

    def test_setting_the_same_level_twice_is_idempotent(self):
        self.client.put(self.url, {"level": Level.NONE}, format="json")
        resp = self.client.put(self.url, {"level": Level.NONE}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self._level(), Level.NONE)


class NotificationLevelReadTests(ChatTestMixin, APITestCase):
    """The header button renders from the serialized conversation, so both
    the list and the detail payload have to carry the caller's own level."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.member)

    def test_list_exposes_the_callers_level(self):
        ConversationMember.objects.filter(
            conversation=self.group,
            user=self.member,
        ).update(notification_level=Level.MENTIONS)

        resp = self.client.get("/api/v1/chat/conversations")
        conv = next(c for c in resp.data if c["uuid"] == str(self.group.uuid))
        self.assertEqual(conv["notification_level"], Level.MENTIONS)

    def test_list_level_is_per_user_not_per_conversation(self):
        """The creator muting the group must not mute it for everyone."""
        ConversationMember.objects.filter(
            conversation=self.group,
            user=self.creator,
        ).update(notification_level=Level.NONE)

        resp = self.client.get("/api/v1/chat/conversations")
        conv = next(c for c in resp.data if c["uuid"] == str(self.group.uuid))
        self.assertEqual(conv["notification_level"], Level.ALL)

    def test_detail_exposes_the_callers_level(self):
        ConversationMember.objects.filter(
            conversation=self.group,
            user=self.member,
        ).update(notification_level=Level.NONE)

        resp = self.client.get(f"/api/v1/chat/conversations/{self.group.uuid}")
        self.assertEqual(resp.data["notification_level"], Level.NONE)

    def test_list_reads_the_level_without_extra_queries(self):
        """The level rides the members prefetch the list already does; it
        must not reintroduce one query per conversation."""
        with CaptureQueriesContext(connection) as ctx:
            self.client.get("/api/v1/chat/conversations")
        baseline = len(ctx.captured_queries)

        for i in range(5):
            conv = self._extra_group(f"Extra {i}")
            ConversationMember.objects.filter(
                conversation=conv,
                user=self.member,
            ).update(notification_level=Level.MENTIONS)

        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get("/api/v1/chat/conversations")
        self.assertEqual(len(ctx.captured_queries), baseline)
        levels = {
            c["notification_level"]
            for c in resp.data
            if c["title"] and c["title"].startswith("Extra")
        }
        self.assertEqual(levels, {Level.MENTIONS})

    def _extra_group(self, title):
        from workspace.chat.models import Conversation

        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title=title,
            created_by=self.creator,
        )
        ConversationMember.objects.create(conversation=conv, user=self.creator)
        ConversationMember.objects.create(conversation=conv, user=self.member)
        return conv
