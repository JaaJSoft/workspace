from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import ConversationMember

from .test_chat import ChatTestMixin


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
