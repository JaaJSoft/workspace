from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.chat.activity import ChatActivityProvider
from workspace.chat.models import Conversation, ConversationMember, Message

User = get_user_model()


class ChatActivityProviderTests(TestCase):

    def setUp(self):
        self.alice = User.objects.create_user(
            username='alice', email='alice@test.com', password='pass123',
        )
        self.bob = User.objects.create_user(
            username='bob', email='bob@test.com', password='pass123',
        )

        self.ts = timezone.now()

        # Alice creates 2 group conversations
        self.alice_group1 = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='Alice Group 1',
            created_by=self.alice,
        )
        self.alice_group2 = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='Alice Group 2',
            created_by=self.alice,
        )

        # Bob creates 1 group conversation
        self.bob_group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='Bob Group',
            created_by=self.bob,
        )

        # All creators are members of their own conversations
        ConversationMember.objects.create(
            conversation=self.alice_group1, user=self.alice,
        )
        ConversationMember.objects.create(
            conversation=self.alice_group2, user=self.alice,
        )
        ConversationMember.objects.create(
            conversation=self.bob_group, user=self.bob,
        )

        # Bob is a member of alice_group1 only
        ConversationMember.objects.create(
            conversation=self.alice_group1, user=self.bob,
        )

        # Messages for stats tests
        Message.objects.create(
            conversation=self.alice_group1, author=self.alice, body='Hello from Alice in group 1',
        )
        Message.objects.create(
            conversation=self.alice_group1, author=self.alice, body='Another msg from Alice in group 1',
        )
        Message.objects.create(
            conversation=self.alice_group2, author=self.alice, body='Hello from Alice in group 2',
        )
        Message.objects.create(
            conversation=self.bob_group, author=self.bob, body='Hello from Bob',
        )

        self.provider = ChatActivityProvider()

    # ── get_daily_counts ──────────────────────────────────

    def test_daily_counts_own_profile(self):
        """Alice's own profile: sees 2 group conversations she created."""
        today = self.ts.date()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today,
        )
        self.assertEqual(counts.get(today, 0), 2)

    def test_daily_counts_viewer_sees_only_member_convs(self):
        """Bob viewing Alice's profile: sees only the 1 conv where Bob is member."""
        today = self.ts.date()
        counts = self.provider.get_daily_counts(
            self.alice.id, today, today, viewer_id=self.bob.id,
        )
        self.assertEqual(counts.get(today, 0), 1)

    # ── get_recent_events ─────────────────────────────────

    def test_recent_events_own_profile(self):
        """Alice sees her 2 group conversations."""
        events = self.provider.get_recent_events(self.alice.id)
        self.assertEqual(len(events), 2)
        titles = {e['description'] for e in events}
        self.assertEqual(titles, {'Alice Group 1', 'Alice Group 2'})
        for e in events:
            self.assertEqual(e['label'], 'Group created')
            self.assertEqual(e['actor']['username'], 'alice')

    def test_recent_events_viewer_sees_only_member_convs(self):
        """Bob viewing Alice's events sees only the 1 group where Bob is member."""
        events = self.provider.get_recent_events(
            self.alice.id, viewer_id=self.bob.id,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['description'], 'Alice Group 1')

    def test_recent_events_excludes_dm(self):
        """DM conversations never appear in recent events."""
        Conversation.objects.create(
            kind=Conversation.Kind.DM,
            title='',
            created_by=self.alice,
        )
        events = self.provider.get_recent_events(self.alice.id)
        self.assertEqual(len(events), 2)  # still only the 2 groups
        for e in events:
            self.assertNotEqual(e['label'], 'DM created')

    # ── get_stats ─────────────────────────────────────────

    def test_stats_own_profile(self):
        """Alice sees her active conversations and total messages."""
        stats = self.provider.get_stats(self.alice.id)
        self.assertEqual(stats['active_conversations'], 2)  # member of 2 groups
        self.assertEqual(stats['total_messages'], 3)  # 3 messages authored

    def test_stats_viewer_restricted(self):
        """Bob viewing Alice's stats: only counts convs/messages in shared convs."""
        stats = self.provider.get_stats(self.alice.id, viewer_id=self.bob.id)
        # Bob is only a member of alice_group1
        self.assertEqual(stats['active_conversations'], 1)
        # Alice has 2 messages in alice_group1
        self.assertEqual(stats['total_messages'], 2)
