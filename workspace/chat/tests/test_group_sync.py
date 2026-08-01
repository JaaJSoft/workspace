from django.contrib.auth.models import Group, User
from django.test import TestCase

from workspace.chat.models import Conversation
from workspace.chat.serializers import ConversationDetailSerializer


class ConversationGroupsModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )
        self.team = Group.objects.create(name="Team A")

    def test_conversation_can_attach_groups(self):
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Chan", created_by=self.user
        )
        conv.groups.add(self.team)
        self.assertEqual(list(self.team.conversations.all()), [conv])

    def test_detail_serializer_exposes_groups(self):
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Chan", created_by=self.user
        )
        conv.groups.add(self.team)
        data = ConversationDetailSerializer(conv).data
        self.assertEqual(data["groups"], [{"id": self.team.pk, "name": "Team A"}])
