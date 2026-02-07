from io import BytesIO

from django.contrib.auth import get_user_model
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember, Message, Reaction

User = get_user_model()


class ChatTestMixin:
    """Common setup for chat tests."""

    def setUp(self):
        self.creator = User.objects.create_user(
            username='creator', email='creator@test.com', password='pass123',
        )
        self.member = User.objects.create_user(
            username='member', email='member@test.com', password='pass123',
        )
        self.outsider = User.objects.create_user(
            username='outsider', email='outsider@test.com', password='pass123',
        )
        self.extra_user = User.objects.create_user(
            username='extra', email='extra@test.com', password='pass123',
        )

        # Create a group conversation owned by creator, with member
        self.group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='Test Group',
            created_by=self.creator,
        )
        ConversationMember.objects.create(
            conversation=self.group, user=self.creator,
        )
        ConversationMember.objects.create(
            conversation=self.group, user=self.member,
        )

        # Create a DM between creator and member
        self.dm = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.creator,
        )
        ConversationMember.objects.create(
            conversation=self.dm, user=self.creator,
        )
        ConversationMember.objects.create(
            conversation=self.dm, user=self.member,
        )


class AddMembersTests(ChatTestMixin, APITestCase):
    """Tests for POST /api/v1/chat/conversations/<id>/members"""

    def url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/members'

    def test_unauthenticated_rejected(self):
        resp = self.client.post(self.url(self.group.uuid), {'user_ids': [self.extra_user.id]})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_cannot_add_members(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(self.url(self.group.uuid), {'user_ids': [self.extra_user.id]}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_can_add_to_group(self):
        self.client.force_authenticate(self.member)
        resp = self.client.post(self.url(self.group.uuid), {'user_ids': [self.extra_user.id]}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(
            ConversationMember.objects.filter(
                conversation=self.group, user=self.extra_user, left_at__isnull=True,
            ).exists()
        )

    def test_creator_can_add_to_group(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.post(self.url(self.group.uuid), {'user_ids': [self.extra_user.id]}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(
            ConversationMember.objects.filter(
                conversation=self.group, user=self.extra_user, left_at__isnull=True,
            ).exists()
        )

    def test_cannot_add_to_dm(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.post(self.url(self.dm.uuid), {'user_ids': [self.extra_user.id]}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_user_ids_rejected(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.post(self.url(self.group.uuid), {'user_ids': [99999]}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_user_ids_rejected(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.post(self.url(self.group.uuid), {'user_ids': []}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_adding_existing_member_is_idempotent(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.post(self.url(self.group.uuid), {'user_ids': [self.member.id]}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Still only one active membership
        self.assertEqual(
            ConversationMember.objects.filter(
                conversation=self.group, user=self.member, left_at__isnull=True,
            ).count(), 1,
        )

    def test_reactivate_left_member(self):
        # Member leaves
        cm = ConversationMember.objects.get(conversation=self.group, user=self.member)
        cm.left_at = timezone.now()
        cm.save()
        # Re-add
        self.client.force_authenticate(self.creator)
        resp = self.client.post(self.url(self.group.uuid), {'user_ids': [self.member.id]}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        cm.refresh_from_db()
        self.assertIsNone(cm.left_at)

    def test_response_includes_new_member(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.post(self.url(self.group.uuid), {'user_ids': [self.extra_user.id]}, format='json')
        member_usernames = [m['user']['username'] for m in resp.data['members']]
        self.assertIn('extra', member_usernames)

    def test_left_member_cannot_add(self):
        cm = ConversationMember.objects.get(conversation=self.group, user=self.member)
        cm.left_at = timezone.now()
        cm.save()
        self.client.force_authenticate(self.member)
        resp = self.client.post(self.url(self.group.uuid), {'user_ids': [self.extra_user.id]}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class RemoveMemberTests(ChatTestMixin, APITestCase):
    """Tests for DELETE /api/v1/chat/conversations/<id>/members/<user_id>"""

    def url(self, conv_id, user_id):
        return f'/api/v1/chat/conversations/{conv_id}/members/{user_id}'

    def test_unauthenticated_rejected(self):
        resp = self.client.delete(self.url(self.group.uuid, self.member.id))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_cannot_remove(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.delete(self.url(self.group.uuid, self.member.id))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_creator_member_cannot_remove(self):
        self.client.force_authenticate(self.member)
        # Add extra_user first
        ConversationMember.objects.create(conversation=self.group, user=self.extra_user)
        resp = self.client.delete(self.url(self.group.uuid, self.extra_user.id))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_creator_can_remove_member(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.delete(self.url(self.group.uuid, self.member.id))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        cm = ConversationMember.objects.get(conversation=self.group, user=self.member)
        self.assertIsNotNone(cm.left_at)

    def test_creator_cannot_remove_self(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.delete(self.url(self.group.uuid, self.creator.id))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_remove_from_dm(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.delete(self.url(self.dm.uuid, self.member.id))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_remove_nonexistent_member(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.delete(self.url(self.group.uuid, self.outsider.id))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_remove_already_left_member(self):
        cm = ConversationMember.objects.get(conversation=self.group, user=self.member)
        cm.left_at = timezone.now()
        cm.save()
        self.client.force_authenticate(self.creator)
        resp = self.client.delete(self.url(self.group.uuid, self.member.id))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_left_creator_cannot_remove(self):
        # Creator leaves the group
        cm = ConversationMember.objects.get(conversation=self.group, user=self.creator)
        cm.left_at = timezone.now()
        cm.save()
        self.client.force_authenticate(self.creator)
        resp = self.client.delete(self.url(self.group.uuid, self.member.id))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class RenameConversationTests(ChatTestMixin, APITestCase):
    """Tests for PATCH /api/v1/chat/conversations/<id> (rename)"""

    def url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}'

    def test_unauthenticated_rejected(self):
        resp = self.client.patch(self.url(self.group.uuid), {'title': 'New'})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_cannot_rename(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.patch(self.url(self.group.uuid), {'title': 'New'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_can_rename_group(self):
        self.client.force_authenticate(self.member)
        resp = self.client.patch(self.url(self.group.uuid), {'title': 'Renamed'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.group.refresh_from_db()
        self.assertEqual(self.group.title, 'Renamed')

    def test_cannot_rename_dm(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.patch(self.url(self.dm.uuid), {'title': 'New'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_title_rejected(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.patch(self.url(self.group.uuid), {'title': '  '}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class LeaveConversationTests(ChatTestMixin, APITestCase):
    """Tests for DELETE /api/v1/chat/conversations/<id> (leave)"""

    def url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}'

    def test_unauthenticated_rejected(self):
        resp = self.client.delete(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_cannot_leave(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.delete(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_can_leave(self):
        self.client.force_authenticate(self.member)
        resp = self.client.delete(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        cm = ConversationMember.objects.get(conversation=self.group, user=self.member)
        self.assertIsNotNone(cm.left_at)

    def test_creator_can_leave(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.delete(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        cm = ConversationMember.objects.get(conversation=self.group, user=self.creator)
        self.assertIsNotNone(cm.left_at)

    def test_already_left_member_rejected(self):
        cm = ConversationMember.objects.get(conversation=self.group, user=self.member)
        cm.left_at = timezone.now()
        cm.save()
        self.client.force_authenticate(self.member)
        resp = self.client.delete(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


def _make_test_image():
    """Create a minimal in-memory PNG for upload tests."""
    buf = BytesIO()
    img = Image.new('RGB', (100, 100), color='red')
    img.save(buf, format='PNG')
    buf.seek(0)
    buf.name = 'test.png'
    return buf


class GroupAvatarUploadTests(ChatTestMixin, APITestCase):
    """Tests for POST /api/v1/chat/conversations/<id>/avatar"""

    def url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/avatar'

    def test_unauthenticated_rejected(self):
        image = _make_test_image()
        resp = self.client.post(self.url(self.group.uuid), {
            'image': image, 'crop_x': 0, 'crop_y': 0, 'crop_w': 100, 'crop_h': 100,
        }, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_cannot_upload(self):
        self.client.force_authenticate(self.outsider)
        image = _make_test_image()
        resp = self.client.post(self.url(self.group.uuid), {
            'image': image, 'crop_x': 0, 'crop_y': 0, 'crop_w': 100, 'crop_h': 100,
        }, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_can_upload(self):
        self.client.force_authenticate(self.member)
        image = _make_test_image()
        resp = self.client.post(self.url(self.group.uuid), {
            'image': image, 'crop_x': 0, 'crop_y': 0, 'crop_w': 100, 'crop_h': 100,
        }, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.group.refresh_from_db()
        self.assertTrue(self.group.has_avatar)

    def test_dm_returns_400(self):
        self.client.force_authenticate(self.creator)
        image = _make_test_image()
        resp = self.client.post(self.url(self.dm.uuid), {
            'image': image, 'crop_x': 0, 'crop_y': 0, 'crop_w': 100, 'crop_h': 100,
        }, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class GroupAvatarDeleteTests(ChatTestMixin, APITestCase):
    """Tests for DELETE /api/v1/chat/conversations/<id>/avatar"""

    def url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/avatar'

    def _upload_avatar(self):
        """Helper to upload an avatar as creator first."""
        self.client.force_authenticate(self.creator)
        image = _make_test_image()
        self.client.post(self.url(self.group.uuid), {
            'image': image, 'crop_x': 0, 'crop_y': 0, 'crop_w': 100, 'crop_h': 100,
        }, format='multipart')

    def test_member_can_delete(self):
        self._upload_avatar()
        self.client.force_authenticate(self.member)
        resp = self.client.delete(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.group.refresh_from_db()
        self.assertFalse(self.group.has_avatar)

    def test_outsider_cannot_delete(self):
        self._upload_avatar()
        self.client.force_authenticate(self.outsider)
        resp = self.client.delete(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class GroupAvatarRetrieveTests(ChatTestMixin, APITestCase):
    """Tests for GET /api/v1/chat/conversations/<id>/avatar/image"""

    def upload_url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/avatar'

    def retrieve_url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/avatar/image'

    def test_404_when_no_avatar(self):
        resp = self.client.get(self.retrieve_url(self.group.uuid))
        self.assertEqual(resp.status_code, 404)

    def test_retrieve_after_upload(self):
        self.client.force_authenticate(self.creator)
        image = _make_test_image()
        self.client.post(self.upload_url(self.group.uuid), {
            'image': image, 'crop_x': 0, 'crop_y': 0, 'crop_w': 100, 'crop_h': 100,
        }, format='multipart')
        self.client.logout()
        resp = self.client.get(self.retrieve_url(self.group.uuid))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/webp')


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


class ConversationMessageSearchTests(ChatTestMixin, APITestCase):
    """Tests for GET /api/v1/chat/conversations/<id>/messages/search?q=..."""

    def url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/messages/search'

    def test_unauthenticated_rejected(self):
        resp = self.client.get(self.url(self.group.uuid), {'q': 'hello'})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_rejected(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url(self.group.uuid), {'q': 'hello'})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_empty_query_returns_400(self):
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'q': ''})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_query_returns_400(self):
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_member_can_search(self):
        Message.objects.create(
            conversation=self.group, author=self.creator, body='hello world',
        )
        Message.objects.create(
            conversation=self.group, author=self.member, body='goodbye world',
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'q': 'hello'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['query'], 'hello')
        self.assertEqual(resp.data['results'][0]['body'], 'hello world')

    def test_case_insensitive_search(self):
        Message.objects.create(
            conversation=self.group, author=self.creator, body='Hello World',
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'q': 'hello'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)

    def test_deleted_messages_excluded(self):
        Message.objects.create(
            conversation=self.group, author=self.creator, body='visible hello',
        )
        Message.objects.create(
            conversation=self.group, author=self.creator, body='deleted hello',
            deleted_at=timezone.now(),
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'q': 'hello'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['body'], 'visible hello')

    def test_no_results(self):
        Message.objects.create(
            conversation=self.group, author=self.creator, body='hello world',
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'q': 'nonexistent'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 0)
        self.assertEqual(resp.data['results'], [])

    def test_results_ordered_newest_first(self):
        msg1 = Message.objects.create(
            conversation=self.group, author=self.creator, body='first hello',
        )
        msg2 = Message.objects.create(
            conversation=self.group, author=self.member, body='second hello',
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'q': 'hello'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 2)
        # Newest first
        self.assertEqual(resp.data['results'][0]['uuid'], str(msg2.uuid))
        self.assertEqual(resp.data['results'][1]['uuid'], str(msg1.uuid))
