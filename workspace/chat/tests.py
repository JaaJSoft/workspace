from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember, Message, MessageAttachment, Reaction

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


class ConversationDescriptionTests(ChatTestMixin, APITestCase):
    """Tests for PATCH /api/v1/chat/conversations/<id> (description)"""

    def url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}'

    def test_member_can_set_description_on_group(self):
        self.client.force_authenticate(self.member)
        resp = self.client.patch(self.url(self.group.uuid), {'description': 'A test description'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.group.refresh_from_db()
        self.assertEqual(self.group.description, 'A test description')

    def test_member_can_set_description_on_dm(self):
        self.client.force_authenticate(self.creator)
        resp = self.client.patch(self.url(self.dm.uuid), {'description': 'DM description'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.dm.refresh_from_db()
        self.assertEqual(self.dm.description, 'DM description')

    def test_description_can_be_cleared(self):
        self.group.description = 'Existing description'
        self.group.save(update_fields=['description'])
        self.client.force_authenticate(self.member)
        resp = self.client.patch(self.url(self.group.uuid), {'description': ''}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.group.refresh_from_db()
        self.assertEqual(self.group.description, '')

    def test_outsider_cannot_update_description(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.patch(self.url(self.group.uuid), {'description': 'Nope'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_title_and_description_together(self):
        self.client.force_authenticate(self.member)
        resp = self.client.patch(self.url(self.group.uuid), {
            'title': 'New Title',
            'description': 'New Desc',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.group.refresh_from_db()
        self.assertEqual(self.group.title, 'New Title')
        self.assertEqual(self.group.description, 'New Desc')

    def test_no_fields_returns_400(self):
        self.client.force_authenticate(self.member)
        resp = self.client.patch(self.url(self.group.uuid), {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_title_on_dm_rejected(self):
        """Title changes are still group-only."""
        self.client.force_authenticate(self.creator)
        resp = self.client.patch(self.url(self.dm.uuid), {'title': 'New'}, format='json')
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

    # ── Filter: author ──────────────────────────────────────

    def test_filter_by_author(self):
        Message.objects.create(conversation=self.group, author=self.creator, body='msg by creator')
        Message.objects.create(conversation=self.group, author=self.member, body='msg by member')

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'author': self.creator.id})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['author']['id'], self.creator.id)

    def test_filter_by_author_combined_with_query(self):
        Message.objects.create(conversation=self.group, author=self.creator, body='hello from creator')
        Message.objects.create(conversation=self.group, author=self.member, body='hello from member')
        Message.objects.create(conversation=self.group, author=self.creator, body='goodbye from creator')

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {
            'q': 'hello', 'author': self.creator.id,
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['body'], 'hello from creator')

    # ── Filter: date_range ──────────────────────────────────

    def test_filter_date_range_today(self):
        msg_today = Message.objects.create(
            conversation=self.group, author=self.creator, body='today msg',
        )
        msg_old = Message.objects.create(
            conversation=self.group, author=self.creator, body='old msg',
        )
        # Push msg_old to 3 days ago
        Message.objects.filter(uuid=msg_old.uuid).update(
            created_at=timezone.now() - timedelta(days=3),
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'date_range': 'today'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['uuid'], str(msg_today.uuid))

    def test_filter_date_range_7d(self):
        msg_recent = Message.objects.create(
            conversation=self.group, author=self.creator, body='recent msg',
        )
        msg_old = Message.objects.create(
            conversation=self.group, author=self.creator, body='old msg',
        )
        Message.objects.filter(uuid=msg_old.uuid).update(
            created_at=timezone.now() - timedelta(days=10),
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'date_range': '7d'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['uuid'], str(msg_recent.uuid))

    def test_filter_date_range_30d(self):
        msg_recent = Message.objects.create(
            conversation=self.group, author=self.creator, body='recent msg',
        )
        msg_old = Message.objects.create(
            conversation=self.group, author=self.creator, body='old msg',
        )
        Message.objects.filter(uuid=msg_old.uuid).update(
            created_at=timezone.now() - timedelta(days=60),
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'date_range': '30d'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['uuid'], str(msg_recent.uuid))

    # ── Filter: custom date range ───────────────────────────

    def test_filter_custom_date_from(self):
        msg_recent = Message.objects.create(
            conversation=self.group, author=self.creator, body='recent',
        )
        msg_old = Message.objects.create(
            conversation=self.group, author=self.creator, body='old',
        )
        Message.objects.filter(uuid=msg_old.uuid).update(
            created_at=timezone.now() - timedelta(days=10),
        )

        date_from = (timezone.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'date_from': date_from})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['uuid'], str(msg_recent.uuid))

    def test_filter_custom_date_to(self):
        msg_recent = Message.objects.create(
            conversation=self.group, author=self.creator, body='recent',
        )
        msg_old = Message.objects.create(
            conversation=self.group, author=self.creator, body='old',
        )
        Message.objects.filter(uuid=msg_old.uuid).update(
            created_at=timezone.now() - timedelta(days=10),
        )

        date_to = (timezone.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'date_to': date_to})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['uuid'], str(msg_old.uuid))

    def test_filter_custom_date_range_both(self):
        msg1 = Message.objects.create(
            conversation=self.group, author=self.creator, body='msg1',
        )
        msg2 = Message.objects.create(
            conversation=self.group, author=self.creator, body='msg2',
        )
        msg3 = Message.objects.create(
            conversation=self.group, author=self.creator, body='msg3',
        )
        # msg1 = 20 days ago, msg2 = 5 days ago, msg3 = today
        Message.objects.filter(uuid=msg1.uuid).update(
            created_at=timezone.now() - timedelta(days=20),
        )
        Message.objects.filter(uuid=msg2.uuid).update(
            created_at=timezone.now() - timedelta(days=5),
        )

        date_from = (timezone.now() - timedelta(days=10)).strftime('%Y-%m-%d')
        date_to = (timezone.now() - timedelta(days=2)).strftime('%Y-%m-%d')
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {
            'date_from': date_from, 'date_to': date_to,
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['uuid'], str(msg2.uuid))

    # ── Filter: has_files / has_images ──────────────────────

    def _attach(self, msg, mime_type='application/pdf', name='file.pdf'):
        return MessageAttachment.objects.create(
            message=msg,
            file=SimpleUploadedFile(name, b'fake-content', content_type=mime_type),
            original_name=name,
            mime_type=mime_type,
            size=12,
        )

    def test_filter_has_files(self):
        msg_with = Message.objects.create(
            conversation=self.group, author=self.creator, body='has attachment',
        )
        Message.objects.create(
            conversation=self.group, author=self.creator, body='no attachment',
        )
        self._attach(msg_with)

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'has_files': 'true'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['uuid'], str(msg_with.uuid))

    def test_filter_has_images(self):
        msg_img = Message.objects.create(
            conversation=self.group, author=self.creator, body='has image',
        )
        msg_pdf = Message.objects.create(
            conversation=self.group, author=self.creator, body='has pdf',
        )
        Message.objects.create(
            conversation=self.group, author=self.creator, body='no attachment',
        )
        self._attach(msg_img, mime_type='image/png', name='photo.png')
        self._attach(msg_pdf, mime_type='application/pdf', name='doc.pdf')

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'has_images': 'true'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['uuid'], str(msg_img.uuid))

    def test_filter_has_files_no_duplicates(self):
        """A message with 2 attachments should appear once, not twice."""
        msg = Message.objects.create(
            conversation=self.group, author=self.creator, body='multi attach',
        )
        self._attach(msg, name='a.pdf')
        self._attach(msg, name='b.pdf')

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'has_files': 'true'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)

    # ── Combined filters ────────────────────────────────────

    def test_combined_author_and_has_files(self):
        msg1 = Message.objects.create(
            conversation=self.group, author=self.creator, body='creator with file',
        )
        msg2 = Message.objects.create(
            conversation=self.group, author=self.member, body='member with file',
        )
        self._attach(msg1)
        self._attach(msg2)

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {
            'author': self.creator.id, 'has_files': 'true',
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['uuid'], str(msg1.uuid))

    def test_combined_query_and_date_range(self):
        msg_today = Message.objects.create(
            conversation=self.group, author=self.creator, body='hello today',
        )
        msg_old = Message.objects.create(
            conversation=self.group, author=self.creator, body='hello old',
        )
        Message.objects.filter(uuid=msg_old.uuid).update(
            created_at=timezone.now() - timedelta(days=3),
        )

        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {
            'q': 'hello', 'date_range': 'today',
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['uuid'], str(msg_today.uuid))

    # ── Validation ──────────────────────────────────────────

    def test_no_criteria_returns_400(self):
        """No q, no filters → 400."""
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filter_only_no_query_is_ok(self):
        """Filters without q should succeed."""
        Message.objects.create(
            conversation=self.group, author=self.creator, body='some message',
        )
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.group.uuid), {'author': self.creator.id})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)


# ── Unread count denormalization tests ─────────────────────────


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

    # ── Send message increments ────────────────────────────────

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

    # ── Mark as read resets ────────────────────────────────────

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

    # ── Delete message decrements ──────────────────────────────

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

    # ── Member re-join resets ──────────────────────────────────

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

    # ── get_unread_counts service ──────────────────────────────

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

    # ── Conversation list integration ──────────────────────────

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

    # ── Multi-member group scenarios ───────────────────────────

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

        # Member sends a message — increments extra_user and creator, not member
        self.client.post(self._msg_url(self.group.uuid), {'body': 'reply'}, format='json')

        self.assertEqual(self._get_unread(self.member, self.group), 0)
        self.assertEqual(self._get_unread(self.extra_user, self.group), 3)
        self.assertEqual(self._get_unread(self.creator, self.group), 1)
