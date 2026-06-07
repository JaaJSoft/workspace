import threading
import unittest
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageInteraction,
)

User = get_user_model()


def _make_question(bot, conv):
    msg = Message.objects.create(
        conversation=conv,
        author=bot,
        body="Quel ton ?",
    )
    interaction = MessageInteraction.objects.create(
        message=msg,
        kind=MessageInteraction.Kind.QUESTION,
        payload={"question": "Quel ton ?", "options": ["Formal", "Casual"]},
    )
    return msg, interaction


class AnswerEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pw",
        )
        self.bot = User.objects.create_user(
            username="bot",
            email="b@test.com",
            password="pw",
        )
        self.outsider = User.objects.create_user(
            username="outsider",
            email="o@test.com",
            password="pw",
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.msg, self.interaction = _make_question(self.bot, self.conv)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = f"/api/v1/chat/messages/{self.msg.uuid}/answer"

    def test_403_when_unauthenticated(self):
        # DRF returns 403 (not 401) under SessionAuthentication when the
        # request lacks credentials, matching the convention used by other
        # chat endpoint tests (see test_readers / test_ui_readers_view).
        self.client.force_authenticate(None)
        resp = self.client.post(self.url, {"option_index": 0}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_404_when_message_unknown(self):
        bad_url = f"/api/v1/chat/messages/{uuid.uuid4()}/answer"
        resp = self.client.post(bad_url, {"option_index": 0}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_404_when_user_not_member(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(self.url, {"option_index": 0}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_400_when_option_index_missing(self):
        resp = self.client.post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_400_when_option_index_not_int(self):
        resp = self.client.post(self.url, {"option_index": "foo"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_400_when_option_index_out_of_range(self):
        resp = self.client.post(self.url, {"option_index": 5}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post(self.url, {"option_index": -1}, format="json")
        self.assertEqual(resp.status_code, 400)

    @patch("workspace.chat.views_interactions._trigger_bot_response")
    def test_201_nominal_creates_answer_and_locks(self, mock_trigger):
        resp = self.client.post(self.url, {"option_index": 0}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.interaction.refresh_from_db()
        self.assertIsNotNone(self.interaction.interacted_at)
        self.assertEqual(self.interaction.interacted_by, self.user)
        self.assertEqual(self.interaction.state["selected_index"], 0)
        answer = Message.objects.get(uuid=self.interaction.state["answer_message_id"])
        self.assertEqual(answer.body, "Formal")
        self.assertEqual(answer.author, self.user)
        self.assertEqual(answer.reply_to, self.msg)
        mock_trigger.assert_called_once()

    @patch("workspace.chat.views_interactions._trigger_bot_response")
    def test_409_when_already_answered_by_other(self, mock_trigger):
        self.client.post(self.url, {"option_index": 0}, format="json")
        bob = User.objects.create_user(
            username="bob",
            email="b@x.com",
            password="pw",
        )
        ConversationMember.objects.create(conversation=self.conv, user=bob)
        self.client.force_authenticate(bob)
        resp = self.client.post(self.url, {"option_index": 1}, format="json")
        self.assertEqual(resp.status_code, 409)

    @patch("workspace.chat.views_interactions._trigger_bot_response")
    def test_200_idempotent_when_same_user_same_choice(self, mock_trigger):
        first = self.client.post(self.url, {"option_index": 0}, format="json")
        self.assertEqual(first.status_code, 201)
        second = self.client.post(self.url, {"option_index": 0}, format="json")
        self.assertEqual(second.status_code, 200)
        mock_trigger.assert_called_once()

    @patch("workspace.chat.views_interactions._trigger_bot_response")
    def test_409_when_same_user_different_choice(self, mock_trigger):
        self.client.post(self.url, {"option_index": 0}, format="json")
        resp = self.client.post(self.url, {"option_index": 1}, format="json")
        self.assertEqual(resp.status_code, 409)


@unittest.skipIf(
    connection.vendor == "sqlite",
    "SELECT FOR UPDATE is a no-op on SQLite - race test requires PostgreSQL",
)
class AnswerEndpointRaceTests(TransactionTestCase):
    """Two concurrent POSTs - one wins (201), other loses (409)."""

    def setUp(self):
        self.user1 = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pw",
        )
        self.user2 = User.objects.create_user(
            username="bob",
            email="b@test.com",
            password="pw",
        )
        self.bot = User.objects.create_user(
            username="bot",
            email="c@test.com",
            password="pw",
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            created_by=self.user1,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user1)
        ConversationMember.objects.create(conversation=self.conv, user=self.user2)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.msg, self.interaction = _make_question(self.bot, self.conv)

    @patch("workspace.chat.views_interactions._trigger_bot_response")
    def test_concurrent_clicks_one_wins(self, mock_trigger):
        url = f"/api/v1/chat/messages/{self.msg.uuid}/answer"
        results = []
        lock = threading.Lock()

        def click(user, index):
            client = APIClient()
            client.force_authenticate(user)
            r = client.post(url, {"option_index": index}, format="json")
            with lock:
                results.append(r.status_code)
            connection.close()

        t1 = threading.Thread(target=click, args=(self.user1, 0))
        t2 = threading.Thread(target=click, args=(self.user2, 1))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertIn(201, results)
        self.assertIn(409, results)
