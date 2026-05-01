from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import ConversationMember, Message, Reaction

from .test_chat import ChatTestMixin


class ConversationStatsTests(ChatTestMixin, APITestCase):
    """Tests for GET /api/v1/chat/conversations/<id>/stats"""

    def url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/stats'

    def test_unauthenticated_rejected(self):
        resp = self.client.get(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_cannot_view_stats(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_can_view_stats(self):
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_empty_conversation_stats(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.get(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['message_count'], 0)
        self.assertEqual(resp.data['reaction_count'], 0)
        self.assertIsNone(resp.data['first_message_at'])
        self.assertIsNone(resp.data['last_message_at'])
        self.assertEqual(resp.data['messages_per_member'], [])

    def test_stats_with_messages_and_reactions(self):
        msg1 = Message.objects.create(
            conversation=self.group, author=self.creator, body='hello',
        )
        msg2 = Message.objects.create(
            conversation=self.group, author=self.member, body='hi',
        )
        Message.objects.create(
            conversation=self.group, author=self.creator, body='third',
        )
        Reaction.objects.create(message=msg1, user=self.member, emoji='👍')
        Reaction.objects.create(message=msg2, user=self.creator, emoji='❤️')

        self.client.force_authenticate(self.creator)
        resp = self.client.get(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['message_count'], 3)
        self.assertEqual(resp.data['reaction_count'], 2)
        self.assertIsNotNone(resp.data['first_message_at'])
        self.assertIsNotNone(resp.data['last_message_at'])
        # Creator has 2 messages, member has 1
        per_member = {e['username']: e['count'] for e in resp.data['messages_per_member']}
        self.assertEqual(per_member['creator'], 2)
        self.assertEqual(per_member['member'], 1)

    def test_deleted_messages_excluded(self):
        Message.objects.create(
            conversation=self.group, author=self.creator, body='visible',
        )
        Message.objects.create(
            conversation=self.group, author=self.creator, body='deleted',
            deleted_at=timezone.now(),
        )

        self.client.force_authenticate(self.creator)
        resp = self.client.get(self.url(self.group.uuid))
        self.assertEqual(resp.data['message_count'], 1)

    def test_reactions_on_deleted_messages_excluded(self):
        deleted_msg = Message.objects.create(
            conversation=self.group, author=self.creator, body='deleted',
            deleted_at=timezone.now(),
        )
        Reaction.objects.create(message=deleted_msg, user=self.member, emoji='👍')

        self.client.force_authenticate(self.creator)
        resp = self.client.get(self.url(self.group.uuid))
        self.assertEqual(resp.data['reaction_count'], 0)


class UnreadCountModelTests(ChatTestMixin, APITestCase):
    """Tests for the denormalized unread_count field on ConversationMember."""

    def _get_unread(self, user, conversation):
        return ConversationMember.objects.get(
            user=user, conversation=conversation,
        ).unread_count

    def _msg_url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/messages'

    def _read_url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/read'

    def _detail_url(self, conv_id, msg_id):
        return f'/api/v1/chat/conversations/{conv_id}/messages/{msg_id}'

    # -- Send message increments --------------------------------

    def test_send_message_increments_other_members(self):
        """Sending a message increments unread_count for all other active members."""
        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'hello'}, format='json')

        self.assertEqual(self._get_unread(self.member, self.group), 1)
        self.assertEqual(self._get_unread(self.creator, self.group), 0)

    def test_send_message_does_not_increment_author(self):
        """The author's own unread_count stays at 0."""
        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'msg1'}, format='json')
        self.client.post(self._msg_url(self.group.uuid), {'body': 'msg2'}, format='json')

        self.assertEqual(self._get_unread(self.creator, self.group), 0)
        self.assertEqual(self._get_unread(self.member, self.group), 2)

    def test_send_message_increments_cumulatively(self):
        """Multiple messages stack up the unread_count."""
        self.client.force_authenticate(self.creator)
        for i in range(5):
            self.client.post(self._msg_url(self.group.uuid), {'body': f'msg {i}'}, format='json')

        self.assertEqual(self._get_unread(self.member, self.group), 5)

    def test_send_message_does_not_increment_left_member(self):
        """A member who left should not get their unread_count incremented."""
        cm = ConversationMember.objects.get(conversation=self.group, user=self.member)
        cm.left_at = timezone.now()
        cm.save(update_fields=['left_at'])

        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'hello'}, format='json')

        self.assertEqual(self._get_unread(self.member, self.group), 0)

    def test_send_message_in_dm(self):
        """DMs also track unread_count correctly."""
        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.dm.uuid), {'body': 'dm msg'}, format='json')

        self.assertEqual(self._get_unread(self.member, self.dm), 1)
        self.assertEqual(self._get_unread(self.creator, self.dm), 0)

    # -- Mark as read resets ------------------------------------

    def test_mark_read_resets_unread_count(self):
        """POST /read resets unread_count to 0."""
        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'msg1'}, format='json')
        self.client.post(self._msg_url(self.group.uuid), {'body': 'msg2'}, format='json')

        self.assertEqual(self._get_unread(self.member, self.group), 2)

        self.client.force_authenticate(self.member)
        resp = self.client.post(self._read_url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.assertEqual(self._get_unread(self.member, self.group), 0)

    def test_mark_read_preserves_last_read_at_when_no_unread(self):
        """POST /read should NOT overwrite last_read_at when there are no unread messages."""
        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'msg1'}, format='json')

        # Member marks as read
        self.client.force_authenticate(self.member)
        self.client.post(self._read_url(self.group.uuid))
        membership = ConversationMember.objects.get(conversation=self.group, user=self.member)
        original_read_at = membership.last_read_at
        self.assertIsNotNone(original_read_at)

        # Call mark read again with no new messages
        self.client.post(self._read_url(self.group.uuid))
        membership.refresh_from_db()
        self.assertEqual(membership.last_read_at, original_read_at)

    def test_mark_read_does_not_affect_other_members(self):
        """Marking read for one user doesn't affect another's count."""
        # Add extra_user to the group
        ConversationMember.objects.create(conversation=self.group, user=self.extra_user)

        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'hello'}, format='json')

        self.assertEqual(self._get_unread(self.member, self.group), 1)
        self.assertEqual(self._get_unread(self.extra_user, self.group), 1)

        # Only member marks read
        self.client.force_authenticate(self.member)
        self.client.post(self._read_url(self.group.uuid))

        self.assertEqual(self._get_unread(self.member, self.group), 0)
        self.assertEqual(self._get_unread(self.extra_user, self.group), 1)

    def test_mark_read_clears_chat_notification(self):
        """POST /read marks the matching chat notification as read."""
        from workspace.notifications.models import Notification

        # Creator sends a message -> creates a notification for member
        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'hey'}, format='json')

        # Simulate the notification that notify_new_message would create
        notif = Notification.objects.create(
            recipient=self.member,
            origin='chat',
            icon='message-square',
            title='creator in Test Group',
            body='hey',
            url=f'/chat/{self.group.uuid}',
            actor=self.creator,
        )
        self.assertIsNone(notif.read_at)

        # Member marks conversation as read
        self.client.force_authenticate(self.member)
        resp = self.client.post(self._read_url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Notification should now be marked as read
        notif.refresh_from_db()
        self.assertIsNotNone(notif.read_at)

    def test_mark_read_does_not_clear_other_conversation_notification(self):
        """POST /read only clears notifications for that specific conversation."""
        from workspace.notifications.models import Notification

        # Create notifications for two different conversations
        notif_group = Notification.objects.create(
            recipient=self.member,
            origin='chat',
            icon='message-square',
            title='creator in Test Group',
            body='hey',
            url=f'/chat/{self.group.uuid}',
            actor=self.creator,
        )
        notif_dm = Notification.objects.create(
            recipient=self.member,
            origin='chat',
            icon='message-square',
            title='creator',
            body='dm msg',
            url=f'/chat/{self.dm.uuid}',
            actor=self.creator,
        )

        # Member marks only the group as read
        self.client.force_authenticate(self.member)
        self.client.post(self._read_url(self.group.uuid))

        # Group notification cleared, DM notification untouched
        notif_group.refresh_from_db()
        notif_dm.refresh_from_db()
        self.assertIsNotNone(notif_group.read_at)
        self.assertIsNone(notif_dm.read_at)

    # -- Delete message decrements ------------------------------

    def test_delete_message_decrements_unread(self):
        """Deleting an unread message decrements unread_count for members who hadn't read it."""
        self.client.force_authenticate(self.creator)
        resp = self.client.post(self._msg_url(self.group.uuid), {'body': 'to delete'}, format='json')
        msg_id = resp.data['uuid']

        self.assertEqual(self._get_unread(self.member, self.group), 1)

        # Creator deletes the message
        resp = self.client.delete(self._detail_url(self.group.uuid, msg_id))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

        self.assertEqual(self._get_unread(self.member, self.group), 0)

    def test_delete_message_does_not_decrement_below_zero(self):
        """unread_count should never go negative (Greatest(..., 0))."""
        # Manually set unread_count to 0 for safety
        cm = ConversationMember.objects.get(conversation=self.group, user=self.member)
        cm.unread_count = 0
        cm.save(update_fields=['unread_count'])

        # Create a message directly (bypassing the view increment)
        msg = Message.objects.create(
            conversation=self.group, author=self.creator, body='direct',
        )

        # Delete via API
        self.client.force_authenticate(self.creator)
        self.client.delete(self._detail_url(self.group.uuid, msg.uuid))

        self.assertEqual(self._get_unread(self.member, self.group), 0)

    def test_delete_already_read_message_does_not_decrement(self):
        """If a member has already read the message, deleting it doesn't decrement their count."""
        self.client.force_authenticate(self.creator)
        resp = self.client.post(self._msg_url(self.group.uuid), {'body': 'msg1'}, format='json')
        msg_id = resp.data['uuid']

        # Member reads the conversation
        self.client.force_authenticate(self.member)
        self.client.post(self._read_url(self.group.uuid))
        self.assertEqual(self._get_unread(self.member, self.group), 0)

        # Creator deletes the message that member already read
        self.client.force_authenticate(self.creator)
        self.client.delete(self._detail_url(self.group.uuid, msg_id))

        # Should stay at 0
        self.assertEqual(self._get_unread(self.member, self.group), 0)

    def test_delete_one_of_multiple_unread(self):
        """Deleting one unread message out of several decrements by exactly 1."""
        self.client.force_authenticate(self.creator)
        resp1 = self.client.post(self._msg_url(self.group.uuid), {'body': 'msg1'}, format='json')
        self.client.post(self._msg_url(self.group.uuid), {'body': 'msg2'}, format='json')
        self.client.post(self._msg_url(self.group.uuid), {'body': 'msg3'}, format='json')

        self.assertEqual(self._get_unread(self.member, self.group), 3)

        self.client.delete(self._detail_url(self.group.uuid, resp1.data['uuid']))
        self.assertEqual(self._get_unread(self.member, self.group), 2)

    # -- Member re-join resets ----------------------------------

    def test_rejoin_resets_unread_count(self):
        """When a member who left is re-added, their unread_count resets to 0."""
        # Creator sends a message
        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'hello'}, format='json')
        self.assertEqual(self._get_unread(self.member, self.group), 1)

        # Member leaves
        cm = ConversationMember.objects.get(conversation=self.group, user=self.member)
        cm.left_at = timezone.now()
        cm.save(update_fields=['left_at'])

        # Re-add member
        resp = self.client.post(
            f'/api/v1/chat/conversations/{self.group.uuid}/members',
            {'user_ids': [self.member.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.assertEqual(self._get_unread(self.member, self.group), 0)

    # -- get_unread_counts service ------------------------------

    def test_get_unread_counts_api(self):
        """GET /api/v1/chat/unread-counts returns correct totals."""
        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'g1'}, format='json')
        self.client.post(self._msg_url(self.group.uuid), {'body': 'g2'}, format='json')
        self.client.post(self._msg_url(self.dm.uuid), {'body': 'd1'}, format='json')

        self.client.force_authenticate(self.member)
        resp = self.client.get('/api/v1/chat/unread-counts')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total'], 3)
        self.assertEqual(resp.data['conversations'][str(self.group.uuid)], 2)
        self.assertEqual(resp.data['conversations'][str(self.dm.uuid)], 1)

    def test_get_unread_counts_excludes_zero(self):
        """Conversations with 0 unread should not appear in the response."""
        self.client.force_authenticate(self.member)
        resp = self.client.get('/api/v1/chat/unread-counts')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total'], 0)
        self.assertEqual(resp.data['conversations'], {})

    def test_get_unread_counts_after_mark_read(self):
        """After marking a conversation read, it disappears from unread counts."""
        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'hi'}, format='json')

        self.client.force_authenticate(self.member)
        resp = self.client.get('/api/v1/chat/unread-counts')
        self.assertEqual(resp.data['total'], 1)

        self.client.post(self._read_url(self.group.uuid))

        resp = self.client.get('/api/v1/chat/unread-counts')
        self.assertEqual(resp.data['total'], 0)
        self.assertNotIn(str(self.group.uuid), resp.data['conversations'])

    def test_get_unread_counts_excludes_left_conversations(self):
        """Left conversations should not appear in unread counts."""
        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'hi'}, format='json')

        # Member leaves
        cm = ConversationMember.objects.get(conversation=self.group, user=self.member)
        cm.left_at = timezone.now()
        cm.save(update_fields=['left_at'])

        self.client.force_authenticate(self.member)
        resp = self.client.get('/api/v1/chat/unread-counts')
        self.assertEqual(resp.data['total'], 0)

    # -- Conversation list integration --------------------------

    def test_conversation_list_shows_unread_count(self):
        """GET /api/v1/chat/conversations includes correct unread_count per conversation."""
        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'g1'}, format='json')
        self.client.post(self._msg_url(self.group.uuid), {'body': 'g2'}, format='json')

        self.client.force_authenticate(self.member)
        resp = self.client.get('/api/v1/chat/conversations')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        conv_map = {c['uuid']: c for c in resp.data}
        self.assertEqual(conv_map[str(self.group.uuid)]['unread_count'], 2)
        self.assertEqual(conv_map[str(self.dm.uuid)]['unread_count'], 0)

    # -- Multi-member group scenarios ---------------------------

    def test_three_members_independent_counts(self):
        """Each member in a group tracks their own independent unread_count."""
        ConversationMember.objects.create(conversation=self.group, user=self.extra_user)

        # Creator sends 2 messages
        self.client.force_authenticate(self.creator)
        self.client.post(self._msg_url(self.group.uuid), {'body': 'msg1'}, format='json')
        self.client.post(self._msg_url(self.group.uuid), {'body': 'msg2'}, format='json')

        self.assertEqual(self._get_unread(self.member, self.group), 2)
        self.assertEqual(self._get_unread(self.extra_user, self.group), 2)
        self.assertEqual(self._get_unread(self.creator, self.group), 0)

        # Member reads
        self.client.force_authenticate(self.member)
        self.client.post(self._read_url(self.group.uuid))

        self.assertEqual(self._get_unread(self.member, self.group), 0)
        self.assertEqual(self._get_unread(self.extra_user, self.group), 2)

        # Member sends a message - increments extra_user and creator, not member
        self.client.post(self._msg_url(self.group.uuid), {'body': 'reply'}, format='json')

        self.assertEqual(self._get_unread(self.member, self.group), 0)
        self.assertEqual(self._get_unread(self.extra_user, self.group), 3)
        self.assertEqual(self._get_unread(self.creator, self.group), 1)
