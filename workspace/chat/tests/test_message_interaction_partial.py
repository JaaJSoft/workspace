from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageInteraction,
)

User = get_user_model()


class MessageInteractionPartialTests(TestCase):
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
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.message = Message.objects.create(
            conversation=self.conv,
            author=self.bot,
            body="Quel ton ?",
        )

    def _render(self, message):
        return render_to_string(
            "chat/ui/partials/_message_interaction.html",
            {"msg": message},
        )

    def test_pending_renders_clickable_buttons(self):
        MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "Q", "options": ["Formal", "Casual"]},
        )
        self.message.refresh_from_db()
        html = self._render(self.message)
        self.assertIn("<button", html)
        self.assertIn("Formal", html)
        self.assertIn("Casual", html)
        self.assertIn("answer(", html)
        self.assertIn('x-data="messageInteraction()"', html)
        self.assertNotIn("pointer-events-none", html)
        self.assertNotIn("btn-primary", html)

    def test_answered_state_renders_non_clickable_with_highlight(self):
        interaction = MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "Q", "options": ["Formal", "Casual"]},
        )
        interaction.interacted_at = timezone.now()
        interaction.interacted_by = self.user
        interaction.state = {"selected_index": 0, "answer_message_id": "abc"}
        interaction.save()
        self.message.refresh_from_db()
        html = self._render(self.message)
        self.assertNotIn("<button", html)
        self.assertIn("pointer-events-none", html)
        self.assertIn("btn-primary", html)
        self.assertIn("opacity-40", html)
        self.assertIn("Formal", html)
        self.assertIn("Casual", html)

    def test_group_shows_answered_by(self):
        group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=group, user=self.user)
        ConversationMember.objects.create(conversation=group, user=self.bot)
        msg = Message.objects.create(
            conversation=group,
            author=self.bot,
            body="Q?",
        )
        interaction = MessageInteraction.objects.create(
            message=msg,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "Q", "options": ["A", "B"]},
        )
        interaction.interacted_at = timezone.now()
        interaction.interacted_by = self.user
        interaction.state = {"selected_index": 0, "answer_message_id": "abc"}
        interaction.save()
        msg.refresh_from_db()
        html = self._render(msg)
        self.assertIn("Répondu par", html)

    def test_dm_hides_answered_by(self):
        interaction = MessageInteraction.objects.create(
            message=self.message,
            kind=MessageInteraction.Kind.QUESTION,
            payload={"question": "Q", "options": ["A", "B"]},
        )
        interaction.interacted_at = timezone.now()
        interaction.interacted_by = self.user
        interaction.state = {"selected_index": 0, "answer_message_id": "abc"}
        interaction.save()
        self.message.refresh_from_db()
        html = self._render(self.message)
        self.assertNotIn("Répondu par", html)


class MessageInteractionQueryCountTests(TestCase):
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
        self.other = User.objects.create_user(
            username="bob",
            email="b@x.com",
            password="pw",
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        ConversationMember.objects.create(conversation=self.conv, user=self.other)

    def _seed_messages(self, n_with_interaction):
        for i in range(n_with_interaction):
            msg = Message.objects.create(
                conversation=self.conv,
                author=self.bot,
                body=f"Question {i}?",
            )
            interaction = MessageInteraction.objects.create(
                message=msg,
                kind=MessageInteraction.Kind.QUESTION,
                payload={"question": f"Question {i}?", "options": ["A", "B"]},
            )
            if i % 2 == 0:
                interaction.interacted_at = timezone.now()
                interaction.interacted_by = self.other
                interaction.state = {
                    "selected_index": 0,
                    "answer_message_id": str(msg.uuid),
                }
                interaction.save()

    def test_ui_partial_view_no_n_plus_1(self):
        from django.test import Client

        client = Client()
        client.force_login(self.user)
        url = f"/chat/{self.conv.uuid}/messages"

        self._seed_messages(2)
        client.get(url)

        from django.conf import settings as dj_settings
        from django.db import connection, reset_queries

        prev_debug = dj_settings.DEBUG
        dj_settings.DEBUG = True
        try:
            Message.objects.filter(conversation=self.conv).delete()
            self._seed_messages(2)
            reset_queries()
            client.get(url)
            baseline_queries = len(connection.queries)

            Message.objects.filter(conversation=self.conv).delete()
            self._seed_messages(10)
            reset_queries()
            client.get(url)
            scaled_queries = len(connection.queries)
        finally:
            dj_settings.DEBUG = prev_debug

        self.assertLessEqual(
            scaled_queries,
            baseline_queries + 5,
            f"N+1 detected: 2 msgs -> {baseline_queries} queries, "
            f"10 msgs -> {scaled_queries} queries (delta={scaled_queries - baseline_queries})",
        )

    def test_serializer_no_n_plus_1(self):
        from django.conf import settings as dj_settings
        from django.db import connection, reset_queries

        from workspace.chat.serializers import MessageSerializer

        def _serialize(n):
            from django.db.models import Prefetch

            from workspace.chat.models import Reaction

            Message.objects.filter(conversation=self.conv).delete()
            self._seed_messages(n)
            qs = (
                Message.objects.filter(conversation=self.conv)
                .select_related(
                    "author",
                    "author__bot_profile",
                    "reply_to",
                    "reply_to__author",
                    "interaction",
                    "interaction__interacted_by",
                )
                .prefetch_related(
                    Prefetch(
                        "reactions",
                        queryset=Reaction.objects.select_related("user"),
                    ),
                    "attachments",
                    "link_previews__preview",
                )
            )
            reset_queries()
            data = MessageSerializer(qs, many=True).data
            return len(connection.queries), len(data)

        prev_debug = dj_settings.DEBUG
        dj_settings.DEBUG = True
        try:
            baseline_q, baseline_n = _serialize(2)
            scaled_q, scaled_n = _serialize(10)
        finally:
            dj_settings.DEBUG = prev_debug

        self.assertEqual(scaled_n, 10)
        self.assertLessEqual(
            scaled_q,
            baseline_q + 5,
            f"N+1 in serializer: 2 msgs -> {baseline_q} queries, "
            f"10 msgs -> {scaled_q} queries (delta={scaled_q - baseline_q})",
        )
