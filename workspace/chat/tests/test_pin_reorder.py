from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation, ConversationMember, PinnedConversation

User = get_user_model()


class ConversationPinReorderTests(APITestCase):
    URL = '/api/v1/chat/conversations/pin-reorder'

    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p')
        self.conv1 = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title='A', created_by=self.user,
        )
        self.conv2 = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title='B', created_by=self.user,
        )
        for c in (self.conv1, self.conv2):
            ConversationMember.objects.create(conversation=c, user=self.user)
        self.pin1 = PinnedConversation.objects.create(
            owner=self.user, conversation=self.conv1, position=0,
        )
        self.pin2 = PinnedConversation.objects.create(
            owner=self.user, conversation=self.conv2, position=1,
        )

    def test_reorder_swaps_positions(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.URL,
            data={'order': [str(self.conv2.uuid), str(self.conv1.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.pin1.refresh_from_db()
        self.pin2.refresh_from_db()
        self.assertEqual(self.pin2.position, 0)
        self.assertEqual(self.pin1.position, 1)

    def test_reorder_with_unhashable_entry_returns_400(self):
        """Regression: a list/dict in the order array used to crash with
        TypeError ("unhashable type: 'dict'") at pin_map.get(uuid_str) and
        surface as 500. Must validate entries and return 4xx.
        """
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.URL,
            data={'order': [{'x': 1}, str(self.conv1.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reorder_with_list_entry_returns_400(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.URL,
            data={'order': [['nested'], str(self.conv1.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reorder_non_list_returns_400(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.URL, data={'order': 'not-a-list'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
