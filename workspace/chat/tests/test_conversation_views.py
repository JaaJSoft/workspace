"""Tests for the Conversation list + create API endpoints.

These tests act as a safety net for PERFORMANCE_AUDIT.md items 2.3, 2.4 and 2.5:
they lock down the response contract and the "no N+1" invariants of
GET/POST /api/v1/chat/conversations before any optimization is attempted.
"""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.ai.models import BotProfile
from workspace.chat.models import Conversation, ConversationMember
from .test_chat import ChatTestMixin

User = get_user_model()


class ConversationListViewTests(ChatTestMixin, APITestCase):
    """GET /api/v1/chat/conversations — response shape + N+1 invariants."""

    URL = '/api/v1/chat/conversations'

    # ── Access + filtering ────────────────────────────────────

    def test_unauthenticated_returns_403(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_lists_conversations_where_user_is_active_member(self):
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        uuids = {c['uuid'] for c in resp.data}
        self.assertIn(str(self.group.uuid), uuids)
        self.assertIn(str(self.dm.uuid), uuids)

    def test_excludes_conversations_where_user_has_left(self):
        cm = ConversationMember.objects.get(
            conversation=self.group, user=self.member,
        )
        cm.left_at = timezone.now()
        cm.save(update_fields=['left_at'])

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.URL)
        uuids = {c['uuid'] for c in resp.data}
        self.assertNotIn(str(self.group.uuid), uuids)

    # ── Response shape — locks in what chat.js reads ─────────

    def test_response_includes_members_array_with_user_info(self):
        """Frontend chat.js reads conv.members for title/avatar/bot fallback."""
        self.client.force_authenticate(self.creator)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.data), 0)
        for conv in resp.data:
            self.assertIn('members', conv)
            self.assertIsInstance(conv['members'], list)
            for m in conv['members']:
                self.assertIn('user', m)
                self.assertIn('id', m['user'])
                self.assertIn('username', m['user'])

    def test_members_array_excludes_left_users(self):
        ConversationMember.objects.create(
            conversation=self.group,
            user=self.extra_user,
            left_at=timezone.now(),
        )
        self.client.force_authenticate(self.creator)
        resp = self.client.get(self.URL)

        group = next(c for c in resp.data if c['uuid'] == str(self.group.uuid))
        user_ids = {m['user']['id'] for m in group['members']}
        self.assertEqual(user_ids, {self.creator.id, self.member.id})

    def test_response_includes_is_bot_conversation_flag(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.get(self.URL)
        for conv in resp.data:
            self.assertIn('is_bot_conversation', conv)
            self.assertIsInstance(conv['is_bot_conversation'], bool)

    def test_is_bot_conversation_true_when_bot_member_present(self):
        bot = User.objects.create_user(username='list-bot', password='p')
        BotProfile.objects.create(user=bot, is_public=True)
        bot_conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.creator,
        )
        ConversationMember.objects.create(conversation=bot_conv, user=self.creator)
        ConversationMember.objects.create(conversation=bot_conv, user=bot)

        self.client.force_authenticate(self.creator)
        resp = self.client.get(self.URL)
        entry = next(c for c in resp.data if c['uuid'] == str(bot_conv.uuid))
        self.assertTrue(entry['is_bot_conversation'])

    def test_is_bot_conversation_false_when_no_bot_member(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.get(self.URL)
        for conv in resp.data:
            self.assertFalse(
                conv['is_bot_conversation'],
                f'conv {conv["uuid"]} unexpectedly flagged as bot conversation',
            )

    # ── member_count (item 2.3) — new field ──────────────────

    def test_response_includes_member_count_field(self):
        """2.3: list response exposes member_count so the frontend can show group size
        without iterating conv.members.length."""
        self.client.force_authenticate(self.creator)
        resp = self.client.get(self.URL)
        for conv in resp.data:
            self.assertIn('member_count', conv)
            self.assertIsInstance(conv['member_count'], int)

    def test_member_count_matches_active_members(self):
        """member_count excludes members who left."""
        ConversationMember.objects.create(
            conversation=self.group,
            user=self.extra_user,
            left_at=timezone.now(),
        )
        self.client.force_authenticate(self.creator)
        resp = self.client.get(self.URL)
        group = next(c for c in resp.data if c['uuid'] == str(self.group.uuid))
        self.assertEqual(group['member_count'], 2)  # creator + member; extra_user left

    # ── N+1 invariants ────────────────────────────────────────

    def test_query_count_does_not_scale_with_conversation_count(self):
        """Adding more conversations must not add queries to the list endpoint."""
        self.client.force_authenticate(self.creator)

        with CaptureQueriesContext(connection) as ctx_baseline:
            self.client.get(self.URL)
        baseline = len(ctx_baseline)

        for i in range(5):
            u = User.objects.create_user(username=f'scale-u{i}', password='p')
            conv = Conversation.objects.create(
                kind=Conversation.Kind.DM, created_by=self.creator,
            )
            ConversationMember.objects.create(conversation=conv, user=self.creator)
            ConversationMember.objects.create(conversation=conv, user=u)

        with CaptureQueriesContext(connection) as ctx_after:
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            len(ctx_after), baseline,
            msg=(
                f'Query count must not scale with conversation count — '
                f'baseline={baseline}, after adding 5 conversations={len(ctx_after)}'
            ),
        )

    def test_query_count_does_not_scale_with_members_per_conversation(self):
        """Adding more members to a conversation must not add queries."""
        self.client.force_authenticate(self.creator)

        with CaptureQueriesContext(connection) as ctx_baseline:
            self.client.get(self.URL)
        baseline = len(ctx_baseline)

        for i in range(10):
            u = User.objects.create_user(username=f'bulk-u{i}', password='p')
            ConversationMember.objects.create(conversation=self.group, user=u)

        with CaptureQueriesContext(connection) as ctx_after:
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            len(ctx_after), baseline,
            msg=(
                f'Query count must not scale with members per conversation — '
                f'baseline={baseline}, after adding 10 members={len(ctx_after)}'
            ),
        )

    def test_query_count_does_not_scale_with_bot_conversation_count(self):
        """is_bot_conversation must not trigger a DB hit per conversation."""
        # Start with a bot conversation already present
        bot1 = User.objects.create_user(username='bot-scale-1', password='p')
        BotProfile.objects.create(user=bot1, is_public=True)
        bot_conv1 = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.creator,
        )
        ConversationMember.objects.create(conversation=bot_conv1, user=self.creator)
        ConversationMember.objects.create(conversation=bot_conv1, user=bot1)

        self.client.force_authenticate(self.creator)
        with CaptureQueriesContext(connection) as ctx_baseline:
            self.client.get(self.URL)
        baseline = len(ctx_baseline)

        # Add 3 more bot conversations
        for i in range(3):
            bot = User.objects.create_user(username=f'bot-scale-x{i}', password='p')
            BotProfile.objects.create(user=bot, is_public=True)
            conv = Conversation.objects.create(
                kind=Conversation.Kind.DM, created_by=self.creator,
            )
            ConversationMember.objects.create(conversation=conv, user=self.creator)
            ConversationMember.objects.create(conversation=conv, user=bot)

        with CaptureQueriesContext(connection) as ctx_after:
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            len(ctx_after), baseline,
            msg=(
                f'is_bot_conversation must not cause N+1 — '
                f'baseline={baseline}, after adding 3 bot conversations={len(ctx_after)}'
            ),
        )


class ConversationCreateViewTests(ChatTestMixin, APITestCase):
    """POST /api/v1/chat/conversations — response shape + bounded query count."""

    URL = '/api/v1/chat/conversations'

    def test_unauthenticated_returns_403(self):
        resp = self.client.post(
            self.URL,
            {'member_ids': [self.extra_user.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── DM (non-bot) ──────────────────────────────────────────

    def test_creates_dm_with_non_bot_user(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.post(
            self.URL,
            {'member_ids': [self.extra_user.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['kind'], 'dm')
        user_ids = {m['user']['id'] for m in resp.data['members']}
        self.assertEqual(user_ids, {self.creator.id, self.extra_user.id})

    def test_dm_is_deduplicated_when_already_exists(self):
        """POST with same peer returns the existing DM (creator+member already exist)."""
        self.client.force_authenticate(self.creator)
        resp = self.client.post(
            self.URL,
            {'member_ids': [self.member.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # Should reuse the DM from ChatTestMixin.setUp
        self.assertEqual(resp.data['uuid'], str(self.dm.uuid))

    def test_cannot_create_dm_with_self(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.post(
            self.URL,
            {'member_ids': [self.creator.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Bot DM ────────────────────────────────────────────────

    def test_creates_bot_dm(self):
        bot = User.objects.create_user(username='create-bot', password='p')
        BotProfile.objects.create(user=bot, is_public=True)

        self.client.force_authenticate(self.creator)
        resp = self.client.post(
            self.URL,
            {'member_ids': [bot.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['kind'], 'dm')
        user_ids = {m['user']['id'] for m in resp.data['members']}
        self.assertEqual(user_ids, {self.creator.id, bot.id})

    def test_bot_dm_is_never_deduplicated(self):
        """Bot conversations always create a fresh DM (no dedup)."""
        bot = User.objects.create_user(username='create-bot-2', password='p')
        BotProfile.objects.create(user=bot, is_public=True)
        self.client.force_authenticate(self.creator)

        resp1 = self.client.post(
            self.URL,
            {'member_ids': [bot.id]},
            format='json',
        )
        resp2 = self.client.post(
            self.URL,
            {'member_ids': [bot.id]},
            format='json',
        )
        self.assertEqual(resp1.status_code, 201)
        self.assertEqual(resp2.status_code, 201)
        self.assertNotEqual(resp1.data['uuid'], resp2.data['uuid'])

    def test_rejects_creation_when_bot_not_accessible(self):
        """Non-public bot with no access grants cannot be DMed by random users."""
        bot = User.objects.create_user(username='private-bot', password='p')
        BotProfile.objects.create(user=bot, is_public=False)

        self.client.force_authenticate(self.outsider)
        resp = self.client.post(
            self.URL,
            {'member_ids': [bot.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── Group ─────────────────────────────────────────────────

    def test_creates_group_with_multiple_members(self):
        u2 = User.objects.create_user(username='grp-u2', password='p')
        self.client.force_authenticate(self.creator)
        resp = self.client.post(
            self.URL,
            {
                'member_ids': [self.extra_user.id, u2.id],
                'title': 'Project Alpha',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['kind'], 'group')
        self.assertEqual(resp.data['title'], 'Project Alpha')
        user_ids = {m['user']['id'] for m in resp.data['members']}
        self.assertEqual(
            user_ids,
            {self.creator.id, self.extra_user.id, u2.id},
        )

    def test_creates_group_persists_all_members_to_db(self):
        """Sanity check: response is not the only source of truth — DB must be right."""
        u2 = User.objects.create_user(username='grp-persist-u2', password='p')
        self.client.force_authenticate(self.creator)
        resp = self.client.post(
            self.URL,
            {
                'member_ids': [self.extra_user.id, u2.id],
                'title': 'Persisted',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        conv = Conversation.objects.get(uuid=resp.data['uuid'])
        active_members = ConversationMember.objects.filter(
            conversation=conv, left_at__isnull=True,
        )
        self.assertEqual(active_members.count(), 3)

    def test_rejects_invalid_user_ids(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.post(
            self.URL,
            {'member_ids': [99999]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Bounded query count (2.4 regression guards) ─────────

    def _count_selects_on(self, ctx, table):
        return sum(
            1 for q in ctx.captured_queries
            if table in q['sql'] and q['sql'].strip().upper().startswith('SELECT')
        )

    def test_create_group_skips_post_insert_conversation_refetch(self):
        """2.4 regression guard: after the conversation INSERT, no SELECT
        on chat_conversation is issued. Pre-2.4 the refetch triggered one.
        """
        u2 = User.objects.create_user(username='group-noref-u2', password='p')
        self.client.force_authenticate(self.creator)
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.post(
                self.URL,
                {'member_ids': [self.extra_user.id, u2.id], 'title': 'NoRefetch'},
                format='json',
            )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            self._count_selects_on(ctx, 'chat_conversation'), 0,
            'Group create must not SELECT chat_conversation (no post-INSERT refetch)',
        )
        # And no re-prefetch on members either.
        self.assertEqual(
            self._count_selects_on(ctx, 'chat_conversationmember'), 0,
            'Group create must not re-SELECT members after bulk_create',
        )

    def test_create_bot_dm_skips_post_insert_conversation_refetch(self):
        """Same invariant as group, applied to the bot-DM path."""
        bot = User.objects.create_user(username='bot-noref', password='p')
        BotProfile.objects.create(user=bot, is_public=True)
        self.client.force_authenticate(self.creator)
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.post(
                self.URL,
                {'member_ids': [bot.id]},
                format='json',
            )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            self._count_selects_on(ctx, 'chat_conversation'), 0,
            'Bot DM create must not SELECT chat_conversation',
        )
        self.assertEqual(
            self._count_selects_on(ctx, 'chat_conversationmember'), 0,
            'Bot DM create must not re-SELECT members after bulk_create',
        )

    def test_create_nonbot_dm_still_refetches_by_design(self):
        """Non-bot DMs go through get_or_create_dm which may return an
        existing conversation whose live member state isn't in memory —
        we keep one SELECT on chat_conversation here for the dedup lookup
        AND one for the refetch+prefetch. This test pins that expectation
        so a future refactor of this path doesn't silently drop refetch
        assumptions.
        """
        u = User.objects.create_user(username='nonbot-ref', password='p')
        self.client.force_authenticate(self.creator)
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.post(
                self.URL,
                {'member_ids': [u.id]},
                format='json',
            )
        self.assertEqual(resp.status_code, 201)
        self.assertGreaterEqual(
            self._count_selects_on(ctx, 'chat_conversation'), 1,
            'Non-bot DM create is expected to SELECT chat_conversation '
            '(get_or_create_dm dedup + refetch); 2.4 does not apply here.',
        )

    def test_create_group_query_count_does_not_scale_with_member_count(self):
        """POST query count must not grow per member added."""
        small_members = [
            User.objects.create_user(username=f'small-{i}', password='p').id
            for i in range(2)
        ]
        large_members = [
            User.objects.create_user(username=f'large-{i}', password='p').id
            for i in range(10)
        ]

        self.client.force_authenticate(self.creator)

        with CaptureQueriesContext(connection) as ctx_small:
            resp = self.client.post(
                self.URL,
                {'member_ids': small_members, 'title': 'Small'},
                format='json',
            )
        self.assertEqual(resp.status_code, 201)
        baseline = len(ctx_small)

        with CaptureQueriesContext(connection) as ctx_large:
            resp = self.client.post(
                self.URL,
                {'member_ids': large_members, 'title': 'Large'},
                format='json',
            )
        self.assertEqual(resp.status_code, 201)

        self.assertEqual(
            len(ctx_large), baseline,
            msg=(
                f'POST query count should not scale with members — '
                f'baseline (2 members)={baseline}, '
                f'with 10 members={len(ctx_large)}'
            ),
        )
