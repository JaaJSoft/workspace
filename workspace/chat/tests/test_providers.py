from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import Conversation, ConversationMember, Message

from .test_chat import ChatTestMixin


class ChatPendingActionProviderTests(ChatTestMixin, TestCase):
    """Tests for the chat pending action provider."""

    def test_pending_actions_returns_unread_count(self):
        ConversationMember.objects.filter(
            conversation=self.group, user=self.member,
        ).update(unread_count=3)
        ConversationMember.objects.filter(
            conversation=self.dm, user=self.member,
        ).update(unread_count=2)

        from workspace.core.module_registry import registry
        counts = registry.get_pending_action_counts(self.member)
        self.assertEqual(counts.get('chat'), 5)

    def test_pending_actions_returns_zero_when_no_unread(self):
        from workspace.core.module_registry import registry
        counts = registry.get_pending_action_counts(self.creator)
        self.assertEqual(counts.get('chat'), 0)


class ChatActivityProviderTests(ChatTestMixin, TestCase):
    """Tests for ChatActivityProvider (group conversation creation only)."""

    def setUp(self):
        super().setUp()
        from workspace.chat.activity import ChatActivityProvider
        self.provider = ChatActivityProvider()

    # -- recent events ------------------------------------------

    def test_recent_events_includes_group_creation(self):
        events = self.provider.get_recent_events(self.creator.id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['label'], 'Group created')
        self.assertEqual(events[0]['description'], 'Test Group')
        self.assertIn(str(self.group.pk), events[0]['url'])

    def test_recent_events_excludes_dm(self):
        """DM conversations should never appear in the activity feed."""
        events = self.provider.get_recent_events(self.creator.id)
        descriptions = [e['description'] for e in events]
        self.assertNotIn('', descriptions)  # dm has no title
        # Only the group should be present
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['description'], 'Test Group')

    def test_recent_events_empty_for_non_creator(self):
        """Users who didn't create any group get no events."""
        events = self.provider.get_recent_events(self.outsider.id)
        self.assertEqual(events, [])

    def test_recent_events_all_users(self):
        """With user_id=None, all group creations are returned."""
        Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title='Second Group',
            created_by=self.member,
        )
        events = self.provider.get_recent_events(None)
        self.assertEqual(len(events), 2)

    def test_recent_events_viewer_visibility(self):
        """An outsider can only see groups they are a member of."""
        events = self.provider.get_recent_events(
            self.creator.id, viewer_id=self.outsider.id,
        )
        self.assertEqual(events, [])

    def test_recent_events_viewer_who_is_member(self):
        """A member can see the group creation event."""
        events = self.provider.get_recent_events(
            self.creator.id, viewer_id=self.member.id,
        )
        self.assertEqual(len(events), 1)

    def test_recent_events_limit_and_offset(self):
        for i in range(5):
            Conversation.objects.create(
                kind=Conversation.Kind.GROUP, title=f'Group {i}',
                created_by=self.creator,
            )
        events = self.provider.get_recent_events(self.creator.id, limit=2, offset=1)
        self.assertEqual(len(events), 2)

    def test_recent_events_actor_fields(self):
        events = self.provider.get_recent_events(self.creator.id)
        actor = events[0]['actor']
        self.assertEqual(actor['id'], self.creator.id)
        self.assertEqual(actor['username'], self.creator.username)

    # -- daily counts -------------------------------------------

    def test_daily_counts_for_creator(self):
        today = self.group.created_at.date()
        counts = self.provider.get_daily_counts(
            self.creator.id, today, today,
        )
        self.assertEqual(counts.get(today), 1)

    def test_daily_counts_excludes_dm(self):
        today = self.dm.created_at.date()
        # creator created both a group and a dm, but only group should count
        counts = self.provider.get_daily_counts(
            self.creator.id, today, today,
        )
        self.assertEqual(counts.get(today), 1)

    def test_daily_counts_empty_range(self):
        from datetime import date as d
        counts = self.provider.get_daily_counts(
            self.creator.id, d(2020, 1, 1), d(2020, 1, 2),
        )
        self.assertEqual(counts, {})

    # -- stats --------------------------------------------------

    def test_stats_counts_messages_and_conversations(self):
        Message.objects.create(
            conversation=self.group, author=self.creator, body='hello',
        )
        Message.objects.create(
            conversation=self.group, author=self.creator, body='world',
        )
        stats = self.provider.get_stats(self.creator.id)
        self.assertEqual(stats['total_messages'], 2)
        self.assertGreaterEqual(stats['active_conversations'], 1)

    def test_stats_excludes_deleted_messages(self):
        Message.objects.create(
            conversation=self.group, author=self.creator, body='visible',
        )
        Message.objects.create(
            conversation=self.group, author=self.creator, body='gone',
            deleted_at=timezone.now(),
        )
        stats = self.provider.get_stats(self.creator.id)
        self.assertEqual(stats['total_messages'], 1)
