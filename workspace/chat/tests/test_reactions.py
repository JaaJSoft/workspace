from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    Reaction,
)
from workspace.chat.services.reactions import (
    DEFAULT_QUICK_REACTIONS,
    QUICK_REACTIONS_LIMIT,
    invalidate_quick_reactions,
    quick_reactions_for,
)

User = get_user_model()


class QuickReactionsServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", email="a@test.com", password="pw"
        )
        self.bob = User.objects.create_user(
            username="bob", email="b@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.alice
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)

    def tearDown(self):
        cache.clear()

    def _msg(self):
        return Message.objects.create(
            conversation=self.conv, author=self.alice, body="x"
        )

    def _react(self, user, emoji, *, days_ago=0):
        # created_at is auto_now_add, so override it with a direct UPDATE.
        r = Reaction.objects.create(message=self._msg(), user=user, emoji=emoji)
        if days_ago:
            Reaction.objects.filter(pk=r.pk).update(
                created_at=timezone.now() - timedelta(days=days_ago)
            )
        return r

    def test_no_history_returns_defaults(self):
        self.assertEqual(quick_reactions_for(self.alice), DEFAULT_QUICK_REACTIONS)

    def test_most_used_emoji_ranks_first(self):
        for _ in range(3):
            self._react(self.alice, "🔥")
        self._react(self.alice, "🎯")
        result = quick_reactions_for(self.alice)
        self.assertEqual(result[0], "🔥")
        self.assertEqual(result[1], "🎯")
        self.assertEqual(len(result), QUICK_REACTIONS_LIMIT)

    def test_defaults_pad_without_duplicates(self):
        # 👍 is also a default; it must appear once, ranked by usage.
        for _ in range(2):
            self._react(self.alice, "👍")
        self._react(self.alice, "🔥")
        result = quick_reactions_for(self.alice)
        self.assertEqual(result[0], "👍")
        self.assertIn("🔥", result)
        self.assertEqual(len(result), QUICK_REACTIONS_LIMIT)
        self.assertEqual(len(set(result)), QUICK_REACTIONS_LIMIT)

    def test_reaction_outside_window_is_ignored(self):
        self._react(self.alice, "🔥", days_ago=40)
        self.assertEqual(quick_reactions_for(self.alice), DEFAULT_QUICK_REACTIONS)

    def test_other_users_history_does_not_count(self):
        for _ in range(3):
            self._react(self.bob, "🔥")
        self.assertEqual(quick_reactions_for(self.alice), DEFAULT_QUICK_REACTIONS)

    def test_result_is_cached_until_invalidated(self):
        with patch(
            "workspace.chat.services.reactions._compute_quick_reactions",
            wraps=lambda user: list(DEFAULT_QUICK_REACTIONS),
        ) as mock_compute:
            quick_reactions_for(self.alice)
            quick_reactions_for(self.alice)
            self.assertEqual(mock_compute.call_count, 1)  # second call hit cache
            invalidate_quick_reactions(self.alice.id)
            quick_reactions_for(self.alice)
            self.assertEqual(
                mock_compute.call_count, 2
            )  # recomputed after invalidation

    def test_toggle_endpoint_invalidates_cache(self):
        self.client.force_login(self.alice)
        msg = self._msg()
        # Prime the cache (no history yet -> defaults).
        self.assertEqual(quick_reactions_for(self.alice), DEFAULT_QUICK_REACTIONS)
        resp = self.client.post(
            f"/api/v1/chat/messages/{msg.uuid}/reactions",
            data={"emoji": "🔥"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        # Cache was invalidated, so the freshly-used emoji now leads the list.
        self.assertEqual(quick_reactions_for(self.alice)[0], "🔥")
