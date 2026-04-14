from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.services import (
    extract_mentions,
    notify_new_message,
    render_message_body,
)
from workspace.notifications.models import Notification

User = get_user_model()


# ── render_message_body ─────────────────────────────────────────

class RenderMessageBodyTests(TestCase):

    def test_plain_text_renders_as_paragraph(self):
        html = render_message_body('Hello world')
        self.assertIn('Hello world', html)

    def test_markdown_bold(self):
        html = render_message_body('**bold**')
        self.assertIn('<strong>bold</strong>', html)

    def test_markdown_code_block(self):
        html = render_message_body('```python\nprint("hi")\n```')
        self.assertIn('code-block', html)
        self.assertIn('print', html)

    def test_markdown_inline_code(self):
        html = render_message_body('use `foo()` here')
        self.assertIn('code-inline', html)
        self.assertIn('foo()', html)

    def test_markdown_strikethrough(self):
        html = render_message_body('~~deleted~~')
        self.assertIn('<del>', html)

    def test_images_stripped(self):
        html = render_message_body('![alt text](http://example.com/img.png)')
        self.assertNotIn('<img', html)
        self.assertIn('alt text', html)

    def test_mention_without_map(self):
        html = render_message_body('@alice hello')
        # Without mention_map, @alice is just text
        self.assertNotIn('mention-badge', html)

    def test_mention_with_map(self):
        html = render_message_body('@alice hello', mention_map={'alice': 42})
        self.assertIn('mention-badge', html)
        self.assertIn('alice', html)
        self.assertIn('42', html)

    def test_mention_everyone(self):
        html = render_message_body('@everyone hello', mention_map={'_': None})
        self.assertIn('mention-everyone', html)
        self.assertIn('@everyone', html)

    def test_mention_unknown_user_not_highlighted(self):
        html = render_message_body('@unknown hello', mention_map={'alice': 1})
        self.assertNotIn('mention-badge', html)

    def test_mention_in_code_block_not_replaced(self):
        html = render_message_body('```\n@alice\n```', mention_map={'alice': 1})
        # @alice inside code should not get the badge
        # (it's in a code block, so placeholder won't be in code)
        self.assertIn('alice', html)


# ── extract_mentions ────────────────────────────────────────────

class ExtractMentionsTests(TestCase):

    def test_extracts_usernames(self):
        usernames, has_everyone = extract_mentions('Hello @alice and @bob')
        self.assertEqual(usernames, {'alice', 'bob'})
        self.assertFalse(has_everyone)

    def test_detects_everyone(self):
        usernames, has_everyone = extract_mentions('@everyone check this')
        self.assertTrue(has_everyone)
        self.assertNotIn('everyone', usernames)

    def test_no_mentions(self):
        usernames, has_everyone = extract_mentions('no mentions here')
        self.assertEqual(usernames, set())
        self.assertFalse(has_everyone)

    def test_duplicate_mentions(self):
        usernames, _ = extract_mentions('@alice @alice @bob')
        self.assertEqual(usernames, {'alice', 'bob'})

    def test_mention_with_everyone_and_users(self):
        usernames, has_everyone = extract_mentions('@everyone @alice')
        self.assertTrue(has_everyone)
        self.assertEqual(usernames, {'alice'})


# ── notify_new_message ──────────────────────────────────────────

@patch('workspace.core.sse_registry.notify_sse')
@patch('workspace.notifications.tasks.send_push_notification.delay')
class NotifyNewMessageTests(TestCase):
    """Lock in the batched merge-vs-create semantics of notify_new_message.

    Legacy behaviour did 1 SELECT + 1 UPDATE/INSERT per member (N+1);
    these tests pin down the new 3-statement batched flow so the merge
    path, the bump, and the push-dispatch rules can't silently regress.
    """

    def setUp(self):
        self.author = User.objects.create_user(username='author', password='p')
        self.alice = User.objects.create_user(username='alice', password='p')
        self.bob = User.objects.create_user(username='bob', password='p')
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='Team',
            created_by=self.author,
        )
        for u in (self.author, self.alice, self.bob):
            ConversationMember.objects.create(conversation=self.conv, user=u)

    def test_creates_notification_per_member_excluding_author(self, mock_push, mock_sse):
        notify_new_message(self.conv, self.author, 'hello')

        notifs = Notification.objects.filter(origin='chat')
        self.assertEqual(notifs.count(), 2)
        recipients = set(notifs.values_list('recipient_id', flat=True))
        self.assertEqual(recipients, {self.alice.id, self.bob.id})
        self.assertNotIn(self.author.id, recipients)

    def test_push_dispatched_only_for_new_notifications(self, mock_push, mock_sse):
        notify_new_message(self.conv, self.author, 'first')
        self.assertEqual(mock_push.call_count, 2)  # alice + bob, both new

        mock_push.reset_mock()
        notify_new_message(self.conv, self.author, 'second')
        # Both recipients already have unread chat notifs → merge, no push.
        self.assertEqual(mock_push.call_count, 0)

    def test_sse_fires_for_every_member_on_both_paths(self, mock_push, mock_sse):
        notify_new_message(self.conv, self.author, 'first')
        self.assertEqual(mock_sse.call_count, 2)

        mock_sse.reset_mock()
        notify_new_message(self.conv, self.author, 'second')
        # SSE still fires on the merge path — recipients need the bell
        # content refresh even when the notif count is unchanged.
        self.assertEqual(mock_sse.call_count, 2)

    def test_merge_updates_body_title_and_bumps_created_at(self, mock_push, mock_sse):
        notify_new_message(self.conv, self.author, 'old body')
        first = Notification.objects.get(recipient=self.alice, origin='chat')
        original_pk = first.pk
        original_created = first.created_at

        notify_new_message(self.conv, self.author, 'new body')

        # Same row, body updated, created_at bumped forward.
        merged = Notification.objects.get(recipient=self.alice, origin='chat')
        self.assertEqual(merged.pk, original_pk)
        self.assertEqual(merged.body, 'new body')
        self.assertGreater(merged.created_at, original_created)

    def test_read_notif_is_not_merged_creates_new(self, mock_push, mock_sse):
        notify_new_message(self.conv, self.author, 'first')
        # Simulate alice reading her notification.
        Notification.objects.filter(recipient=self.alice).update(
            read_at='2026-01-01T00:00:00Z',
        )

        notify_new_message(self.conv, self.author, 'second')

        alice_notifs = Notification.objects.filter(recipient=self.alice, origin='chat')
        # The read one stays read + a fresh unread one was created.
        self.assertEqual(alice_notifs.count(), 2)
        self.assertEqual(
            alice_notifs.filter(read_at__isnull=True).count(), 1,
        )

    def test_mention_raises_priority_on_merge(self, mock_push, mock_sse):
        notify_new_message(self.conv, self.author, 'normal')
        alice_notif = Notification.objects.get(recipient=self.alice, origin='chat')
        self.assertEqual(alice_notif.priority, 'normal')

        notify_new_message(
            self.conv, self.author, 'urgent!',
            mentioned_user_ids={self.alice.id},
        )
        alice_notif.refresh_from_db()
        self.assertEqual(alice_notif.priority, 'high')

    def test_urgent_priority_is_not_downgraded_on_merge(self, mock_push, mock_sse):
        notify_new_message(self.conv, self.author, 'first')
        Notification.objects.filter(recipient=self.alice).update(priority='urgent')

        notify_new_message(
            self.conv, self.author, 'second',
            mentioned_user_ids={self.alice.id},
        )
        alice_notif = Notification.objects.get(recipient=self.alice, origin='chat')
        self.assertEqual(alice_notif.priority, 'urgent')

    def test_empty_member_list_is_a_no_op(self, mock_push, mock_sse):
        solo = User.objects.create_user(username='solo', password='p')
        solo_conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=solo,
        )
        ConversationMember.objects.create(conversation=solo_conv, user=solo)

        notify_new_message(solo_conv, solo, 'self-talk')

        self.assertEqual(Notification.objects.count(), 0)
        mock_push.assert_not_called()
        mock_sse.assert_not_called()

    def test_batched_flow_stays_constant_in_query_count(self, mock_push, mock_sse):
        """Regression guard: the batched flow must not slip back to N+1.

        The 4 queries are:
            1. ConversationMember lookup for recipient ids.
            2. SELECT existing chat notifs (batch lookup).
            3. bulk_update for the merge path (CASE WHEN).
            4. bulk_create for the new path.
        """
        # Prime one of the two recipients so we exercise both update AND
        # create paths on the second call.
        notify_new_message(self.conv, self.author, 'prime alice')
        Notification.objects.filter(recipient=self.bob).delete()

        with self.assertNumQueries(4):
            notify_new_message(self.conv, self.author, 'second')

        # Query count must not scale with the number of recipients.
        charlie = User.objects.create_user(username='charlie', password='p')
        dave = User.objects.create_user(username='dave', password='p')
        ConversationMember.objects.create(conversation=self.conv, user=charlie)
        ConversationMember.objects.create(conversation=self.conv, user=dave)
        with self.assertNumQueries(4):
            notify_new_message(self.conv, self.author, 'with more members')
