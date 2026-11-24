import io
from dataclasses import replace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from PIL import Image
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart
from pydantic_ai.usage import RunUsage

from workspace.ai.models import AITask, BotProfile
from workspace.ai.prompts.base import prompt
from workspace.ai.services.bot_runs import BotRun, StopReason, run_bot
from workspace.ai.services.responses import handle_generation_error, post_bot_message
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    MessageAttachment,
    MessageInteraction,
)
from workspace.notifications.models import Notification

from .agents import call, reply, scripted

User = get_user_model()


def _run(*messages, context=None, stop=StopReason.ANSWERED, **fields):
    """The run that added *messages* to a conversation, as run_bot hands it back."""
    response = next(m for m in reversed(messages) if isinstance(m, ModelResponse))
    fields.setdefault("usage", RunUsage())
    return BotRun(
        output=response.text or "",
        response=response,
        messages=list(messages),
        new_messages=list(messages),
        context=context if context is not None else {},
        stop=stop,
        **fields,
    )


def _results(*calls):
    """What the model is sent back after a round that made *calls*."""
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name=made.tool_name, content="ok", tool_call_id=made.tool_call_id
            )
            for made in calls
        ]
    )


class PostBotMessageInteractionTests(TestCase):
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
        self.ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CHAT,
        )

    def test_creates_interaction_when_context_has_question(self):
        tool_context = {
            "question": {
                "question": "Quel ton ?",
                "options": ["Formal", "Casual"],
            },
        }
        body, msg = post_bot_message(
            self.conv,
            self.bot,
            _run(reply("Quel ton ?"), context=tool_context),
            self.ai_task,
        )
        interaction = MessageInteraction.objects.get(message=msg)
        self.assertEqual(interaction.kind, MessageInteraction.Kind.QUESTION)
        self.assertEqual(interaction.payload["question"], "Quel ton ?")
        self.assertEqual(interaction.payload["options"], ["Formal", "Casual"])
        self.assertIsNone(interaction.interacted_at)

    def test_no_interaction_when_context_missing_question(self):
        body, msg = post_bot_message(
            self.conv, self.bot, _run(reply("Quel ton ?")), self.ai_task
        )
        self.assertFalse(MessageInteraction.objects.filter(message=msg).exists())


class PostBotMessageUsageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.bot = User.objects.create_user(username="bot", password="pw")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.ai_task = AITask.objects.create(
            owner=self.user, task_type=AITask.TaskType.CHAT
        )

    def test_the_run_total_lands_on_the_task_not_the_final_call(self):
        answer = replace(reply("hi", input_tokens=30, output_tokens=8), model_name="m")
        run = _run(
            answer,
            usage=RunUsage(input_tokens=50, output_tokens=18),
            seconds=3.5,
            answer_seconds=1.0,
        )

        post_bot_message(self.conv, self.bot, run, self.ai_task)

        self.ai_task.refresh_from_db()
        self.assertEqual(self.ai_task.model_used, "m")
        self.assertEqual(self.ai_task.prompt_tokens, 50)
        self.assertEqual(self.ai_task.completion_tokens, 18)
        self.assertEqual(self.ai_task.generation_seconds, 3.5)
        # The answer's own figures ride along, for the output speed
        self.assertEqual(self.ai_task.answer_tokens, 8)
        self.assertEqual(self.ai_task.answer_seconds, 1.0)

    def test_for_a_single_reply_the_response_is_the_whole_usage(self):
        bot_profile = BotProfile.objects.create(user=self.bot, system_prompt="Bot.")
        with scripted(reply("hi", input_tokens=30, output_tokens=8), model="m"):
            run = run_bot(
                bot_profile,
                self.user,
                self.bot,
                str(self.conv.pk),
                prompt("You are a bot.", "hi"),
            )

        post_bot_message(self.conv, self.bot, run, self.ai_task)

        self.ai_task.refresh_from_db()
        self.assertEqual(self.ai_task.model_used, "m")
        self.assertEqual(self.ai_task.prompt_tokens, 30)
        self.assertEqual(self.ai_task.completion_tokens, 8)
        self.assertEqual(self.ai_task.answer_tokens, 8)
        self.assertIsNotNone(self.ai_task.generation_seconds)
        self.assertEqual(self.ai_task.generation_seconds, self.ai_task.answer_seconds)


@patch("workspace.notifications.services.notifications.notify_sse")
@patch("workspace.notifications.tasks.send_push_notification.delay")
class PostBotMessageNotificationTests(TestCase):
    """A bot reply must reach the notification pipeline, not just SSE.

    Only the SSE cache flag used to fire, so a bot reply never created a
    Notification row and never produced a web push - the recipient got a
    badge and nothing else.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.bot = User.objects.create_user(username="bot", password="pw")
        BotProfile.objects.create(user=self.bot)
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CHAT,
        )
        self.run = _run(reply("Voici la réponse."))

    def tearDown(self):
        cache.clear()

    def test_reply_notifies_the_human_member_and_dispatches_push(
        self, mock_push, mock_sse
    ):
        with self.captureOnCommitCallbacks(execute=True):
            post_bot_message(self.conv, self.bot, self.run, self.ai_task)

        notif = Notification.objects.get(origin="chat")
        self.assertEqual(notif.recipient_id, self.user.id)
        self.assertEqual(notif.actor_id, self.bot.id)
        self.assertEqual(notif.body, "Voici la réponse.")
        self.assertEqual(notif.url, f"/chat/{self.conv.pk}")
        self.assertEqual(notif.conversation_id, self.conv.pk)
        mock_push.assert_called_once_with(str(notif.uuid))

    def test_bot_itself_is_never_a_recipient(self, mock_push, mock_sse):
        with self.captureOnCommitCallbacks(execute=True):
            post_bot_message(self.conv, self.bot, self.run, self.ai_task)

        self.assertFalse(Notification.objects.filter(recipient=self.bot).exists())

    def test_generation_error_notifies_too(self, mock_push, mock_sse):
        with self.captureOnCommitCallbacks(execute=True):
            handle_generation_error(
                self.conv, self.bot, self.ai_task, RuntimeError("backend down")
            )

        notif = Notification.objects.get(origin="chat")
        self.assertEqual(notif.recipient_id, self.user.id)
        # The generic chat-facing text, never the provider error.
        self.assertNotIn("backend down", notif.body)
        mock_push.assert_called_once_with(str(notif.uuid))


def _image_bytes(fmt):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color="red").save(buf, format=fmt)
    return buf.getvalue()


class PostBotMessageImageTypingTests(TestCase):
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
        self.ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CHAT,
        )

    def _post(self, images, *rounds):
        run = _run(*rounds, reply("Voilà l'image"), context={"images": images})
        return post_bot_message(self.conv, self.bot, run, self.ai_task)

    def test_generated_png_is_typed_from_content(self):
        _, msg = self._post([{"data": _image_bytes("PNG"), "prompt": "a red square"}])

        att = MessageAttachment.objects.get(message=msg)
        self.assertEqual(att.type, "png")
        self.assertEqual(att.category, "image")
        self.assertEqual(att.mime_type, "image/png")
        self.assertTrue(att.original_name.endswith(".png"))
        self.assertTrue(att.is_image)

    def test_generated_jpeg_is_not_mislabelled_as_png(self):
        _, msg = self._post([{"data": _image_bytes("JPEG"), "prompt": "a red square"}])

        att = MessageAttachment.objects.get(message=msg)
        self.assertEqual(att.type, "jpeg")
        self.assertEqual(att.category, "image")
        self.assertEqual(att.mime_type, "image/jpeg")
        self.assertFalse(att.original_name.endswith(".png"))

    def test_images_are_attached_in_the_order_the_model_asked_for(self):
        # Generated in parallel, so the second prompt can come back first;
        # the reply describes them in the order it asked for them.
        cat = call("generate_image", {"prompt": "the cat"}, "call_cat")
        dog = call("generate_image", {"prompt": "the dog"}, "call_dog")
        _, msg = self._post(
            [
                {
                    "data": _image_bytes("PNG"),
                    "prompt": "the dog",
                    "call_id": "call_dog",
                },
                {
                    "data": _image_bytes("JPEG"),
                    "prompt": "the cat",
                    "call_id": "call_cat",
                },
            ],
            reply("", cat, dog),
            _results(cat, dog),
        )

        names = [
            att.original_name
            for att in MessageAttachment.objects.filter(message=msg).order_by(
                "created_at"
            )
        ]
        self.assertEqual(names, ["the_cat_1.jpg", "the_dog_2.png"])

    def test_images_of_no_known_call_keep_the_order_they_arrived_in(self):
        _, msg = self._post(
            [
                {"data": _image_bytes("PNG"), "prompt": "first"},
                {"data": _image_bytes("JPEG"), "prompt": "second"},
            ]
        )

        names = [
            att.original_name
            for att in MessageAttachment.objects.filter(message=msg).order_by(
                "created_at"
            )
        ]
        self.assertEqual(names, ["first_1.png", "second_2.jpg"])

    def test_undetectable_bytes_keep_the_png_fallback(self):
        _, msg = self._post([{"data": b"", "prompt": "broken"}])

        att = MessageAttachment.objects.get(message=msg)
        self.assertEqual(att.type, "unknown")
        self.assertEqual(att.category, "unknown")
        self.assertEqual(att.mime_type, "image/png")
        self.assertTrue(att.is_image)


class PostBotMessageThinkingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dave", email="d@test.com", password="pw"
        )
        self.bot = User.objects.create_user(
            username="bot3", email="b3@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot)
        self.ai_task = AITask.objects.create(
            owner=self.user, task_type=AITask.TaskType.CHAT
        )

    def _response(self, thinking=""):
        return reply("Hello", thinking=thinking)

    def _post(self, *messages, **fields):
        _, msg = post_bot_message(
            self.conv, self.bot, _run(*messages, **fields), self.ai_task
        )
        return msg

    def _round(self, thinking):
        """A reply that called a tool, and what the tool came back with."""
        made = call("t", {}, "c1")
        return reply("", made, thinking=thinking), _results(made)

    def test_final_thinking_creates_tool_data_when_none(self):
        msg = self._post(self._response(thinking="final reasoning"))
        self.assertEqual(
            msg.tool_data,
            [{"thinking": "final reasoning", "tool_calls": [], "results": []}],
        )

    def test_final_thinking_appends_to_existing_rounds(self):
        msg = self._post(
            *self._round("round thinking"),
            self._response(thinking="final reasoning"),
        )
        self.assertEqual(len(msg.tool_data), 2)
        self.assertEqual(msg.tool_data[0]["thinking"], "round thinking")
        self.assertEqual(
            msg.tool_data[0]["results"], [{"tool_call_id": "c1", "content": "ok"}]
        )
        self.assertEqual(msg.tool_data[-1]["thinking"], "final reasoning")
        self.assertEqual(msg.tool_data[-1]["tool_calls"], [])

    def test_no_thinking_leaves_tool_data_untouched(self):
        msg = self._post(self._response(thinking=""))
        self.assertIsNone(msg.tool_data)

    def test_duplicate_of_last_round_thinking_is_not_appended(self):
        # stop_after_round case: the posted result IS the last tool round,
        # whose thinking is already persisted in tool_data.
        msg = self._post(*self._round("same thought"), stop=StopReason.AWAITING_USER)
        self.assertEqual(len(msg.tool_data), 1)
        self.assertEqual(msg.tool_data[0]["thinking"], "same thought")


class PostBotMessageCaptionEnqueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="erin", email="e@test.com", password="pw"
        )
        self.bot_user = User.objects.create_user(
            username="bot4", email="b4@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.bot_user)
        self.ai_task = AITask.objects.create(
            owner=self.user, task_type=AITask.TaskType.CHAT
        )

    @override_settings(AI_API_KEY="k")
    def test_generated_images_enqueue_captions(self):
        tool_context = {"images": [{"data": b"\x89PNGfake", "prompt": "a cat"}]}
        with patch(
            "workspace.ai.tasks.captions.generate_attachment_caption.delay"
        ) as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                post_bot_message(
                    self.conv,
                    self.bot_user,
                    _run(reply("here is your image"), context=tool_context),
                    self.ai_task,
                )
        mock_delay.assert_called_once()
