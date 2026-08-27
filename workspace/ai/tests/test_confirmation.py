from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from workspace.ai.services.confirmation import (
    CONFIRM_OPTIONS,
    consume_bound_confirmation,
    request_bound_confirmation,
    request_confirmation,
)

User = get_user_model()
CONVERSATION = "11111111-1111-1111-1111-111111111111"


class RequestConfirmationTests(SimpleTestCase):
    def test_asks_the_question_and_halts_the_loop(self):
        context = {}
        result = request_confirmation(context, "Cancel the standup?")

        self.assertEqual(context["question"]["question"], "Cancel the standup?")
        self.assertEqual(context["question"]["options"], CONFIRM_OPTIONS)
        self.assertTrue(context["stop_after_round"])
        self.assertIn("confirm=true", result)

    def test_first_question_of_a_round_wins(self):
        context = {}
        request_confirmation(context, "First?")
        result = request_confirmation(context, "Second?")

        self.assertEqual(context["question"]["question"], "First?")
        self.assertTrue(context["stop_after_round"])
        # The result must name the prompt the user can actually see, or the
        # model treats their answer to "First?" as approval of "Second?".
        self.assertIn("First?", result)
        self.assertNotIn("Second?", result)


class BoundConfirmationTests(TestCase):
    """The action-bound variant: the payload is pinned server-side.

    ``request_confirmation`` trusts the model to repeat the same call. Where
    a changed argument on the confirming call would be worse than no
    confirmation at all, the payload is stored instead and the model only
    ever holds an opaque token.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="confirmer", password="pw")

    def tearDown(self):
        cache.clear()

    def _pin(self, context=None, user=None, conversation_id=CONVERSATION, **payload):
        return request_bound_confirmation(
            context if context is not None else {},
            "Send this?",
            action="test.write",
            user=user or self.user,
            conversation_id=conversation_id,
            payload=payload or {"to": "alice@example.test"},
        )

    def test_pinning_halts_the_loop_and_returns_the_payload_once(self):
        context = {}
        token, blocked = self._pin(context, to="alice@example.test")

        self.assertIsNone(blocked)
        self.assertTrue(context["stop_after_round"])
        self.assertEqual(context["question"]["question"], "Send this?")
        self.assertEqual(
            consume_bound_confirmation("test.write", self.user, CONVERSATION, token),
            {"to": "alice@example.test"},
        )

    def test_a_token_is_single_use(self):
        token, _ = self._pin()
        consume_bound_confirmation("test.write", self.user, CONVERSATION, token)
        self.assertIsNone(
            consume_bound_confirmation("test.write", self.user, CONVERSATION, token)
        )

    def test_a_token_is_bound_to_its_action(self):
        token, _ = self._pin()
        self.assertIsNone(
            consume_bound_confirmation("other.write", self.user, CONVERSATION, token)
        )

    def test_a_token_is_bound_to_its_user(self):
        stranger = User.objects.create_user(username="stranger", password="pw")
        token, _ = self._pin()
        self.assertIsNone(
            consume_bound_confirmation("test.write", stranger, CONVERSATION, token)
        )

    def test_a_token_is_bound_to_its_conversation(self):
        token, _ = self._pin()
        self.assertIsNone(
            consume_bound_confirmation(
                "test.write", self.user, "22222222-2222-2222-2222-222222222222", token
            )
        )

    def test_an_unknown_token_is_simply_absent(self):
        self.assertIsNone(
            consume_bound_confirmation("test.write", self.user, CONVERSATION, "nope")
        )
        self.assertIsNone(
            consume_bound_confirmation("test.write", self.user, CONVERSATION, "")
        )

    def test_nothing_is_pinned_when_another_question_holds_the_round(self):
        context = {"question": {"question": "First?", "options": ["a", "b"]}}
        token, blocked = self._pin(context)

        self.assertIsNone(token)
        self.assertIn("First?", blocked)
        # The round still halts - the other question is the one the user sees.
        self.assertTrue(context["stop_after_round"])
        self.assertEqual(context["question"]["question"], "First?")

    def test_custom_options_replace_the_default_pair(self):
        context = {}
        request_bound_confirmation(
            context,
            "Send this?",
            action="test.write",
            user=self.user,
            conversation_id=CONVERSATION,
            payload={},
            options=["Yes, send it", "No, keep it as a draft"],
        )
        self.assertEqual(
            context["question"]["options"], ["Yes, send it", "No, keep it as a draft"]
        )
        self.assertNotEqual(context["question"]["options"], CONFIRM_OPTIONS)
