import base64
import json
import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from workspace.ai.models import AITask, BotProfile
from workspace.ai.services.conversation_history import ConversationHistory
from workspace.ai.tests.agents import (
    RecordingTools,
    call,
    reply,
    scripted,
    tool_context,
    tool_results,
    write_call,
)
from workspace.ai.tests.chat_tasks import queue_chat_task
from workspace.ai.tool_registry import ToolError, ToolRegistry
from workspace.ai.tools import EditImageParams, GenerateImageParams
from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.mail.models import MailAccount, MailFolder, MailLabel, MailMessage

User = get_user_model()


def _recording_tools():
    """:class:`RecordingTools`, and the patch that swaps them for the registered tools."""
    tools = RecordingTools()
    registry = ToolRegistry()
    registry.register_provider(tools)
    return tools, patch("workspace.ai.services.bot_runs.tool_registry", registry)


@override_settings(
    AI_API_KEY="test-key",
    AI_MODEL="gpt-4o-mini",
    AI_MAX_TOKENS=100,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class GenerateChatResponseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="user", password="pass123")
        self.bot_user = User.objects.create_user(username="bot", password="pass123")
        self.bot_profile = BotProfile.objects.create(
            user=self.bot_user,
            system_prompt="You are a test bot.",
            is_public=True,
        )
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.user
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.bot_user
        )
        self.message = Message.objects.create(
            conversation=self.conversation,
            author=self.user,
            body="Hello bot!",
        )

    def test_generates_response(self):
        from workspace.ai.tasks.chat import generate_chat_response

        # Repeated: the title of a new conversation is asked of the same model.
        with scripted(
            reply("Hello human!", input_tokens=10, output_tokens=5), repeat=True
        ):
            result = generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        self.assertEqual(result["status"], "ok")
        bot_msg = Message.objects.filter(author=self.bot_user).first()
        self.assertIsNotNone(bot_msg)
        self.assertEqual(bot_msg.body, "Hello human!")

    def test_an_untitled_conversation_gets_a_title_after_the_reply(self):
        from workspace.ai.tasks.chat import generate_chat_response

        with (
            scripted(reply("Hello human!")),
            patch("workspace.ai.tasks.chat.generate_conversation_title.delay") as title,
        ):
            generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        title.assert_called_once_with(str(self.conversation.pk))

    def test_a_titled_conversation_does_not_count_its_messages(self):
        from workspace.ai.tasks.chat import generate_chat_response

        self.conversation.title = "Already named"
        self.conversation.save(update_fields=["title"])
        with (
            scripted(reply("Hello human!")),
            patch("workspace.ai.tasks.chat.generate_conversation_title.delay") as title,
            # The summary trigger counts on its own account; this is about the title.
            patch("workspace.ai.tasks.chat.maybe_dispatch_summary_update"),
            CaptureQueriesContext(connection) as queries,
        ):
            generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        title.assert_not_called()
        message_counts = [
            q["sql"]
            for q in queries.captured_queries
            if "COUNT(" in q["sql"] and '"chat_message"' in q["sql"]
        ]
        self.assertEqual(message_counts, [])

    def test_handles_api_error(self):
        from workspace.ai.tasks.chat import generate_chat_response

        def failing(messages, tools):
            raise RuntimeError("API Error")

        with scripted(failing):
            result = generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        self.assertEqual(result["status"], "error")
        task = AITask.objects.filter(task_type="chat").first()
        self.assertEqual(task.status, AITask.Status.FAILED)

    def test_a_reply_cancelled_while_queued_never_runs(self):
        from workspace.ai.tasks.chat import generate_chat_response

        task_id = queue_chat_task(self.conversation, self.message, self.bot_user)
        AITask.objects.filter(pk=task_id).update(
            status=AITask.Status.FAILED, error="Cancelled by user"
        )

        with scripted() as script:
            result = generate_chat_response(task_id)

        self.assertEqual(result, {"status": "cancelled"})
        self.assertEqual(script.requests, [])
        self.assertFalse(Message.objects.filter(author=self.bot_user).exists())
        task = AITask.objects.get(pk=task_id)
        self.assertEqual(task.status, AITask.Status.FAILED)
        self.assertEqual(task.error, "Cancelled by user")

    def test_the_run_claims_the_task(self):
        from workspace.ai.tasks.chat import generate_chat_response

        seen = []
        task_id = queue_chat_task(self.conversation, self.message, self.bot_user)

        def record_the_status(*args, **kwargs):
            seen.append(AITask.objects.get(pk=task_id).status)
            raise RuntimeError("stop here")

        # The run itself, not a scripted reply: a model answers off the thread
        # that holds the test's database connection.
        with patch("workspace.ai.tasks.chat.run_bot", side_effect=record_the_status):
            generate_chat_response(task_id)

        self.assertEqual(seen[0], AITask.Status.PROCESSING)

    def test_a_reply_to_a_vanished_conversation_is_closed(self):
        from workspace.ai.tasks.chat import generate_chat_response

        task_id = queue_chat_task(self.conversation, self.message, self.bot_user)
        self.conversation.delete()

        result = generate_chat_response(task_id)

        self.assertEqual(result["status"], "error")
        self.assertEqual(AITask.objects.get(pk=task_id).status, AITask.Status.FAILED)


@override_settings(
    AI_API_KEY="test-key",
    AI_MODEL="gpt-4o-mini",
    AI_MAX_TOKENS=100,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class SummarizeTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="user", password="pass123")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            folder_type="inbox",
        )
        self.message = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
            subject="Test email",
            body_text="Hello, this is a test email with important content.",
        )

    def test_summary_persisted_on_message(self):
        task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.SUMMARIZE,
            input_data={"message_id": str(self.message.uuid)},
        )

        from workspace.ai.tasks.mail import summarize

        with scripted(
            reply("• Key point from email", input_tokens=50, output_tokens=10)
        ):
            result = summarize(str(task.uuid))

        self.assertEqual(result["status"], "ok")
        self.message.refresh_from_db()
        self.assertEqual(self.message.ai_summary, "• Key point from email")

    def test_re_summarize_overwrites(self):
        self.message.ai_summary = "Old summary"
        self.message.save(update_fields=["ai_summary"])

        task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.SUMMARIZE,
            input_data={"message_id": str(self.message.uuid)},
        )

        from workspace.ai.tasks.mail import summarize

        with scripted(reply("• New summary", input_tokens=50, output_tokens=10)):
            summarize(str(task.uuid))

        self.message.refresh_from_db()
        self.assertEqual(self.message.ai_summary, "• New summary")


@override_settings(
    AI_API_KEY="test-key",
    AI_MODEL="gpt-4o-mini",
    AI_MAX_TOKENS=100,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class GenerateChatResponseWithToolsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="user", password="pass123")
        self.bot_user = User.objects.create_user(username="bot", password="pass123")
        self.bot_profile = BotProfile.objects.create(
            user=self.bot_user,
            system_prompt="You are a test bot.",
            is_public=True,
        )
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
            title="Test conversation",
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.user
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.bot_user
        )
        self.message = Message.objects.create(
            conversation=self.conversation,
            author=self.user,
            body="My name is Pierre",
        )

    def test_tool_call_saves_memory_then_responds(self):
        from workspace.ai.tasks.chat import generate_chat_response

        with scripted(
            reply(
                "",
                call("save_memory", {"key": "name", "content": "Pierre"}, "call_abc"),
                input_tokens=20,
                output_tokens=10,
            ),
            reply("Got it, Pierre!", input_tokens=30, output_tokens=8),
        ) as script:
            result = generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        self.assertEqual(result["status"], "ok")

        # Memory was saved
        from workspace.ai.models import UserMemory

        mem = UserMemory.objects.get(user=self.user, bot=self.bot_user, key="name")
        self.assertEqual(mem.content, "Pierre")

        # Final response posted
        bot_msg = Message.objects.filter(author=self.bot_user).first()
        self.assertEqual(bot_msg.body, "Got it, Pierre!")

        # Tool usage is persisted on tool_data, not baked into body_html
        self.assertNotIn("Retained:", bot_msg.body_html)

        # The task carries the whole run, not the final call alone
        ai_task = AITask.objects.get(chat_message=bot_msg)
        self.assertEqual(ai_task.prompt_tokens, 50)
        self.assertEqual(ai_task.completion_tokens, 18)
        self.assertIsInstance(bot_msg.tool_data, list)
        tool_names = [
            tc["function"]["name"]
            for td_round in bot_msg.tool_data
            for tc in td_round["tool_calls"]
        ]
        self.assertIn("save_memory", tool_names)

        # Two model requests were made
        self.assertEqual(len(script.requests), 2)

    @patch("workspace.ai.tasks.chat.build_conversation_history")
    def test_the_task_row_exists_before_the_history_is_assembled(self, mock_history):
        """This row is how the UI knows a response is under way.

        Assembling the history reads every attachment in the conversation and
        takes minutes on an image-heavy one. Recorded after that, a reload
        during the window finds nothing and shows no indicator.
        """
        seen = {}

        def record_whether_the_task_is_visible(*args, **kwargs):
            seen["in_flight"] = AITask.objects.filter(
                task_type=AITask.TaskType.CHAT,
                status__in=[AITask.Status.PENDING, AITask.Status.PROCESSING],
            ).exists()
            return ConversationHistory([], "", [])

        mock_history.side_effect = record_whether_the_task_is_visible

        from workspace.ai.tasks.chat import generate_chat_response

        with scripted(reply("Hello")):
            generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        self.assertTrue(seen["in_flight"])

    def test_cancelling_stops_the_run_instead_of_only_dropping_its_answer(self):
        """A cancelled generation must stop working, not finish and be discarded.

        Tools keep writing memories, scheduling messages and billing images
        until the loop ends, so cancellation has to be read inside it.
        """

        def cancel_while_running(call_id):
            # What BotCancelView does when the user hits cancel.
            AITask.objects.filter(task_type=AITask.TaskType.CHAT).update(
                status=AITask.Status.FAILED, error="Cancelled by user"
            )

        tools, registered = _recording_tools()
        tools.gate = cancel_while_running

        from workspace.ai.tasks.chat import generate_chat_response

        # A second round and an answer are available on purpose: a run that
        # goes to the end consumes them, which is what the counts below pin.
        with (
            registered,
            scripted(
                reply("", write_call("call_1", "a")),
                reply("", write_call("call_2", "b")),
                reply("All done"),
            ) as script,
        ):
            result = generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(tools.executed, ["call_1"])
        self.assertEqual(len(script.requests), 1)
        self.assertFalse(Message.objects.filter(author=self.bot_user).exists())

    def test_empty_response_retry_does_not_replay_tools(self):
        """Regression for #143/#144: an empty reply after a tool round is asked
        for again on the same conversation, never by running the reply a
        second time. Tools with side effects (sending messages, mutating
        state) would otherwise fire twice.
        """
        tools, registered = _recording_tools()

        from workspace.ai.tasks.chat import generate_chat_response

        with (
            registered,
            scripted(
                reply("", write_call("call_abc"), input_tokens=10, output_tokens=5),
                reply("", input_tokens=10),
                reply("Got it, Pierre!", input_tokens=15, output_tokens=5),
            ) as script,
        ):
            result = generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(tools.executed, ["call_abc"])
        # The request that got the answer carried the one result, not a second run.
        self.assertEqual(len(script.requests), 3)
        self.assertEqual(tool_results(script.sent), [("call_abc", "written")])
        bot_msg = Message.objects.get(author=self.bot_user)
        self.assertEqual(bot_msg.body, "Got it, Pierre!")


@override_settings(AI_IMAGE_RETRY_DELAY=0)
class GenerateImageToolTest(TestCase):
    """Unit tests for the generate_image tool."""

    def setUp(self):
        from workspace.ai.tools import ImageToolProvider

        self.provider = ImageToolProvider()
        self.conv_id = "test-conv-img"
        self.context = {}

    @patch("workspace.ai.services.image.get_image_client")
    def test_generate_image_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(b64_json=base64.b64encode(b"\x89PNG fake").decode())
        ]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = self.provider.generate_image(
            tool_context(conversation_id=self.conv_id, context=self.context),
            GenerateImageParams(prompt="a cat"),
        )

        self.assertIn("successfully", result)
        self.assertEqual(len(self.context["images"]), 1)
        self.assertEqual(self.context["images"][0]["prompt"], "a cat")
        mock_client.images.generate.assert_called_once()

    def test_generate_image_empty_prompt(self):
        with self.assertRaises(ToolError) as caught:
            self.provider.generate_image(
                tool_context(conversation_id=self.conv_id, context=self.context),
                GenerateImageParams(prompt=""),
            )
        self.assertIn("prompt is required", caught.exception.reason)
        self.assertNotIn("images", self.context)

    def test_generate_image_no_conversation(self):
        with self.assertRaises(ToolError) as caught:
            self.provider.generate_image(
                tool_context(context=self.context),
                GenerateImageParams(prompt="a cat"),
            )
        self.assertIn("no conversation context", caught.exception.reason)

    @patch("workspace.ai.services.image.get_image_client")
    def test_generate_image_invalid_size_defaults(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(b64_json=base64.b64encode(b"\x89PNG fake").decode())
        ]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        self.provider.generate_image(
            tool_context(conversation_id=self.conv_id, context=self.context),
            GenerateImageParams(prompt="a cat", size="999x999"),
        )
        call_kwargs = mock_client.images.generate.call_args[1]
        self.assertEqual(call_kwargs["size"], "1024x1024")

    @patch("workspace.ai.services.image.get_image_client")
    def test_generate_image_api_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.images.generate.side_effect = Exception("API timeout")
        mock_get_client.return_value = mock_client

        with self.assertRaises(ToolError) as caught:
            self.provider.generate_image(
                tool_context(conversation_id=self.conv_id, context=self.context),
                GenerateImageParams(prompt="a cat"),
            )
        self.assertIn("API timeout", caught.exception.reason)
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("images", self.context)

    @patch("workspace.ai.services.image.get_image_client")
    def test_generate_image_empty_data_reports_error(self, mock_get_client):
        """A successful API call that returns no image must not report success."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = []
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        with self.assertRaises(ToolError) as caught:
            self.provider.generate_image(
                tool_context(conversation_id=self.conv_id, context=self.context),
                GenerateImageParams(prompt="a cat"),
            )
        self.assertIn("returned no image", caught.exception.reason)
        self.assertNotIn("images", self.context)

    @patch("workspace.ai.services.image.get_image_client")
    def test_generate_image_malformed_b64_reports_error(self, mock_get_client):
        """A malformed base64 payload must surface as an error, not crash."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(b64_json="!!!not-base64!!!")]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        with self.assertRaises(ToolError) as caught:
            self.provider.generate_image(
                tool_context(conversation_id=self.conv_id, context=self.context),
                GenerateImageParams(prompt="a cat"),
            )
        self.assertIn("returned no image", caught.exception.reason)
        self.assertNotIn("images", self.context)

    @patch("workspace.ai.services.image.get_image_client")
    def test_generate_image_empty_b64_reports_error(self, mock_get_client):
        """An empty b64_json payload must surface as an error, not success."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(b64_json="")]
        mock_client.images.generate.return_value = mock_response
        mock_get_client.return_value = mock_client

        with self.assertRaises(ToolError) as caught:
            self.provider.generate_image(
                tool_context(conversation_id=self.conv_id, context=self.context),
                GenerateImageParams(prompt="a cat"),
            )
        self.assertIn("returned no image", caught.exception.reason)
        self.assertNotIn("images", self.context)


@override_settings(AI_IMAGE_RETRY_DELAY=0, AI_IMAGE_FAILURE_BUDGET=4)
class ImageFailureGuidanceTests(TestCase):
    """What the bot is told once the service has spent its own attempts.

    Retrying is then up to the model, and it only does it if the tool
    result says so, with wording it can act on.
    """

    def setUp(self):
        from workspace.ai.tools import ImageToolProvider

        self.provider = ImageToolProvider()
        self.context = {}

    def _fail_with(self, error, prompt="a cat"):
        client = MagicMock()
        client.images.generate.side_effect = error
        with (
            patch("workspace.ai.services.image.get_image_client", return_value=client),
            self.assertRaises(ToolError) as caught,
        ):
            self.provider.generate_image(
                tool_context(conversation_id="conv-1", context=self.context),
                GenerateImageParams(prompt=prompt),
            )
        return caught.exception

    def _rejection(self):
        import httpx2
        from openai import BadRequestError

        request = httpx2.Request("POST", "http://image-backend/v1/images/generations")
        return BadRequestError(
            "prompt rejected", response=httpx2.Response(400, request=request), body=None
        )

    def test_a_rejected_prompt_is_sent_back_for_a_rewrite(self):
        failure = self._fail_with(self._rejection())

        self.assertIn("rejected the request itself", failure.reason)
        self.assertIn("rewritten prompt", failure.reason)
        self.assertFalse(failure.retryable)

    def test_a_broken_backend_does_not_blame_the_prompt(self):
        # Telling the model to rewrite a prompt the service never read
        # sends it chasing a problem that isn't there.
        failure = self._fail_with(RuntimeError("upstream down"))

        self.assertIn("image service failed, not the prompt", failure.reason)
        self.assertNotIn("rejected the request itself", failure.reason)
        self.assertTrue(failure.retryable)

    def test_resending_the_same_prompt_is_called_out(self):
        self._fail_with(self._rejection())
        failure = self._fail_with(self._rejection())

        self.assertIn("exact prompt already failed", failure.reason)
        self.assertIn("substantially different wording", failure.reason)
        self.assertFalse(failure.retryable)

    def test_a_varied_prompt_is_not_treated_as_a_repeat(self):
        self._fail_with(self._rejection(), prompt="a cat")
        failure = self._fail_with(self._rejection(), prompt="a small tabby kitten")

        self.assertNotIn("exact prompt already failed", failure.reason)

    def test_the_reply_is_told_to_stop_once_the_budget_is_spent(self):
        # Without a cap, a model that keeps rewriting can burn the whole
        # tool-round budget on one image and hang the reply.
        for _ in range(3):
            failure = self._fail_with(RuntimeError("upstream down"))
            self.assertNotIn("stop calling", failure.reason)

        failure = self._fail_with(RuntimeError("upstream down"))

        self.assertIn("stop calling generate_image", failure.reason)
        self.assertFalse(failure.retryable)

    def test_the_budget_is_shared_with_edit_image(self):
        from workspace.ai.services.image import ImageGenerationError
        from workspace.ai.tools import _image_failure

        for _ in range(3):
            self._fail_with(RuntimeError("upstream down"))

        failure = _image_failure(
            "edit_image",
            "make it blue",
            ImageGenerationError("upstream down", attempts=3),
            self.context,
        )

        self.assertIn("stop calling edit_image", failure.reason)
        self.assertFalse(failure.retryable)


@override_settings(AI_IMAGE_RETRY_DELAY=0)
class EditImageToolTest(TestCase):
    """Unit tests for the edit_image tool."""

    def setUp(self):
        from workspace.ai.tools import ImageToolProvider

        self.provider = ImageToolProvider()
        self.context = {}
        self.user = User.objects.create_user(username="editimguser", password="pw")
        self.conv = Conversation.objects.create(kind="dm", created_by=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.user)

    def _attach_image(self):
        """Create a message with an image attachment in the conversation."""
        from django.core.files.base import ContentFile

        from workspace.chat.models import MessageAttachment

        msg = Message.objects.create(
            conversation=self.conv, author=self.user, body="here"
        )
        att = MessageAttachment(
            message=msg,
            original_name="photo.png",
            mime_type="image/png",
            size=8,
        )
        att.file.save("photo.png", ContentFile(b"\x89PNGdata"), save=False)
        att.save()
        return att

    @patch("workspace.ai.services.image.get_image_client")
    def test_edit_image_openai_success(self, mock_get_client):
        """OpenAI images.edit works on first try."""
        self._attach_image()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(b64_json=base64.b64encode(b"\x89PNG edited").decode())
        ]
        mock_client.images.edit.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = self.provider.edit_image(
            tool_context(
                user=self.user,
                conversation_id=str(self.conv.uuid),
                context=self.context,
            ),
            EditImageParams(prompt="make it blue"),
        )

        self.assertIn("successfully", result)
        self.assertEqual(len(self.context["images"]), 1)
        self.assertEqual(self.context["images"][0]["prompt"], "make it blue")
        mock_client.images.edit.assert_called_once()

    @override_settings(
        AI_IMAGE_MODEL="test-model",
        AI_IMAGE_BASE_URL="http://localhost:11434/v1/",
        AI_TIMEOUT=30,
    )
    @patch("workspace.ai.services.image.get_image_client")
    def test_edit_image_ollama_fallback(self, mock_get_client):
        """Falls back to Ollama native API when OpenAI endpoint fails."""
        self._attach_image()
        mock_client = MagicMock()
        mock_client.images.edit.side_effect = Exception("400 Bad Request")
        mock_get_client.return_value = mock_client

        with patch(
            "workspace.ai.services.image._edit_via_ollama",
            return_value=b"\x89PNG ollama",
        ) as mock_ollama:
            result = self.provider.edit_image(
                tool_context(
                    user=self.user,
                    conversation_id=str(self.conv.uuid),
                    context=self.context,
                ),
                EditImageParams(prompt="make it red"),
            )

        self.assertIn("successfully", result)
        self.assertEqual(len(self.context["images"]), 1)
        mock_ollama.assert_called_once()

    def test_edit_image_empty_prompt(self):
        with self.assertRaises(ToolError) as caught:
            self.provider.edit_image(
                tool_context(conversation_id=str(self.conv.uuid), context=self.context),
                EditImageParams(prompt=""),
            )
        self.assertIn("prompt is required", caught.exception.reason)

    def test_edit_image_no_image_in_conversation(self):
        with self.assertRaises(ToolError) as caught:
            self.provider.edit_image(
                tool_context(
                    user=self.user,
                    conversation_id=str(self.conv.uuid),
                    context=self.context,
                ),
                EditImageParams(prompt="make it blue"),
            )
        self.assertIn("no image found", caught.exception.reason)

    @patch("workspace.ai.services.image.get_image_client")
    def test_edit_image_both_backends_fail(self, mock_get_client):
        """Returns error when both OpenAI and Ollama fail."""
        self._attach_image()
        mock_client = MagicMock()
        mock_client.images.edit.side_effect = Exception("OpenAI failed")
        mock_get_client.return_value = mock_client

        with (
            patch(
                "workspace.ai.services.image._edit_via_ollama",
                side_effect=Exception("Ollama failed"),
            ),
            self.assertRaises(ToolError) as caught,
        ):
            self.provider.edit_image(
                tool_context(
                    user=self.user,
                    conversation_id=str(self.conv.uuid),
                    context=self.context,
                ),
                EditImageParams(prompt="make it blue"),
            )

        self.assertIn("Ollama failed", caught.exception.reason)
        self.assertNotIn("images", self.context)

    @patch("workspace.ai.services.image.get_image_client")
    def test_edit_image_error_tells_the_model_to_call_again(self, mock_get_client):
        self._attach_image()
        mock_client = MagicMock()
        mock_client.images.edit.side_effect = Exception("OpenAI failed")
        mock_get_client.return_value = mock_client

        with (
            patch(
                "workspace.ai.services.image._edit_via_ollama",
                side_effect=Exception("Ollama failed"),
            ),
            self.assertRaises(ToolError) as caught,
        ):
            self.provider.edit_image(
                tool_context(
                    user=self.user,
                    conversation_id=str(self.conv.uuid),
                    context=self.context,
                ),
                EditImageParams(prompt="make it blue"),
            )

        self.assertIn("image service failed", caught.exception.reason)
        self.assertTrue(caught.exception.retryable)

    def test_edit_image_no_conversation(self):
        with self.assertRaises(ToolError) as caught:
            self.provider.edit_image(
                tool_context(context=self.context),
                EditImageParams(prompt="make it blue"),
            )
        self.assertIn("no conversation context", caught.exception.reason)

    @patch("workspace.ai.services.image.get_image_client")
    def test_edit_image_empty_result_reports_error(self, mock_get_client):
        """A backend that returns no image bytes must not report success."""
        self._attach_image()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(b64_json="")]
        mock_client.images.edit.return_value = mock_response
        mock_get_client.return_value = mock_client

        with (
            patch(
                "workspace.ai.services.image._edit_via_ollama",
                side_effect=Exception("Ollama failed"),
            ),
            self.assertRaises(ToolError) as caught,
        ):
            self.provider.edit_image(
                tool_context(
                    user=self.user,
                    conversation_id=str(self.conv.uuid),
                    context=self.context,
                ),
                EditImageParams(prompt="make it blue"),
            )

        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("images", self.context)


def _labelling(*batches):
    """A model that answers each batch of emails with the ``results`` given for it."""
    return scripted(
        *(
            reply(json.dumps({"results": results}), input_tokens=10, output_tokens=5)
            for results in batches
        )
    )


def _stored_replies(ai_task):
    """``(text, finish reason)`` of each reply kept in the task's raw messages."""
    return [
        (
            "".join(
                part["content"]
                for part in message["parts"]
                if part["part_kind"] == "text"
            ),
            message["finish_reason"],
        )
        for message in ai_task.raw_messages["messages"]
        if message["kind"] == "response"
    ]


@override_settings(
    AI_API_KEY="test-key",
    AI_MODEL="gpt-4o-mini",
    AI_SMALL_MODEL="gpt-4o-mini",
    AI_MAX_TOKENS=100,
    CELERY_TASK_ALWAYS_EAGER=False,
)
class ClassifyMailMessagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="clsuser", password="pass123")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="cls@test.com",
            imap_host="imap.test.com",
            smtp_host="smtp.test.com",
            username="cls@test.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.msg1 = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
            subject="Server down!",
            snippet="Production is down",
            from_name="Alert",
            from_email="alert@ops.com",
        )
        self.msg2 = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=2,
            subject="Weekly digest",
            snippet="Here is your digest",
            from_name="News",
            from_email="news@co.com",
        )
        self.label_urgent = self.account.labels.get(name="Urgent")
        self.label_newsletter = self.account.labels.get(name="Newsletter")

    def test_the_message_queryset_takes_more_messages_than_a_statement_binds(self):
        from workspace.ai.tasks.mail import _classify_message_queryset

        limit = 50
        message_uuids = [str(uuid.uuid4()) for _ in range(limit * 2)]
        message_uuids.append(str(self.msg1.uuid))
        with sqlite_parameter_limit(limit):
            fetched = list(_classify_message_queryset(self.user, message_uuids))
        self.assertEqual([message.pk for message in fetched], [self.msg1.pk])

    def test_classifies_messages_with_labels(self):
        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": [str(self.msg1.uuid), str(self.msg2.uuid)]},
        )
        from workspace.ai.tasks.mail import classify_mail_messages

        with _labelling(
            [{"i": 1, "labels": ["Urgent"]}, {"i": 2, "labels": ["Newsletter"]}]
        ):
            classify_mail_messages(str(ai_task.uuid))

        from workspace.mail.models import MailMessageLabel

        self.assertTrue(
            MailMessageLabel.objects.filter(
                message=self.msg1, label=self.label_urgent
            ).exists()
        )
        self.assertTrue(
            MailMessageLabel.objects.filter(
                message=self.msg2, label=self.label_newsletter
            ).exists()
        )
        ai_task.refresh_from_db()
        self.assertEqual(ai_task.status, AITask.Status.COMPLETED)

    def test_classify_prompt_receives_sender_fields(self):
        """The prompt payload must carry each message's sender columns."""
        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": [str(self.msg1.uuid), str(self.msg2.uuid)]},
        )
        from workspace.ai.prompts.mail import build_classify_messages
        from workspace.ai.tasks.mail import classify_mail_messages

        with (
            _labelling([]),
            patch(
                "workspace.ai.prompts.mail.build_classify_messages",
                wraps=build_classify_messages,
            ) as mock_build,
        ):
            classify_mail_messages(str(ai_task.uuid))

        emails = mock_build.call_args[0][0]
        self.assertEqual(
            [(e["from_name"], e["from_email"]) for e in emails],
            [("Alert", "alert@ops.com"), ("News", "news@co.com")],
        )

    def test_classify_prompt_receives_message_metadata(self):
        """Addressing, folder, date and content flags reach the prompt payload."""
        from datetime import UTC, datetime

        msg = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=3,
            subject="Invoice",
            snippet="Attached",
            from_name="Billing",
            from_email="billing@acme.com",
            reply_to_addresses=[{"name": "", "email": "no-reply@acme.com"}],
            to_addresses=[{"name": "", "email": "team@acme.com"}],
            cc_addresses=[{"name": "", "email": "cls@test.com"}],
            date=datetime(2026, 8, 30, 9, 12, tzinfo=UTC),
            in_reply_to="<parent@acme.com>",
            has_attachments=True,
            has_calendar_event=True,
        )
        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": [str(msg.uuid)]},
        )
        from workspace.ai.prompts.mail import build_classify_messages
        from workspace.ai.tasks.mail import classify_mail_messages

        with (
            _labelling([]),
            patch(
                "workspace.ai.prompts.mail.build_classify_messages",
                wraps=build_classify_messages,
            ) as mock_build,
        ):
            classify_mail_messages(str(ai_task.uuid))

        payload = mock_build.call_args[0][0][0]
        self.assertEqual(payload["recipient_role"], "cc")
        self.assertEqual(payload["recipient_count"], 2)
        self.assertEqual(payload["reply_to"], "no-reply@acme.com")
        self.assertEqual(payload["folder"], "Inbox")
        self.assertEqual(payload["date"], msg.date)
        self.assertTrue(payload["has_attachments"])
        self.assertTrue(payload["has_calendar_event"])
        self.assertTrue(payload["is_reply"])

    def test_classify_prompt_receives_label_descriptions(self):
        """The label block carries what the user said belongs in each label."""
        MailLabel.objects.create(
            account=self.account,
            name="Suivi",
            description="Client follow-ups I still owe an answer",
        )
        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": [str(self.msg1.uuid)]},
        )
        from workspace.ai.prompts.mail import build_classify_messages
        from workspace.ai.tasks.mail import classify_mail_messages

        with (
            _labelling([]),
            patch(
                "workspace.ai.prompts.mail.build_classify_messages",
                wraps=build_classify_messages,
            ) as mock_build,
        ):
            classify_mail_messages(str(ai_task.uuid))

        labels = mock_build.call_args[0][1]
        self.assertIn(
            {"name": "Suivi", "description": "Client follow-ups I still owe an answer"},
            labels,
        )

    def test_classifies_across_multiple_batches(self):
        """Messages beyond CLASSIFY_BATCH_SIZE must still be classified.

        Exercises the itertools.batched chunking: with 12 messages and a
        batch size of 10, the task issues two LLM calls, and the per-batch
        index (i=1, i=2, ...) restarts each batch. A regression in the
        batching boundary would leave the 11th/12th messages unlabelled or
        mis-mapped.
        """
        from workspace.ai.tasks.mail import CLASSIFY_BATCH_SIZE

        total = CLASSIFY_BATCH_SIZE + 2
        messages = [
            MailMessage.objects.create(
                account=self.account,
                folder=self.folder,
                imap_uid=100 + n,
                subject=f"Message {n}",
                snippet=f"Body {n}",
                from_name="Sender",
                from_email=f"s{n}@co.com",
            )
            for n in range(total)
        ]

        # First batch: the last message (index 10 -> i=10) is Urgent.
        # Second batch: the first message (index 11 -> i=1) is Newsletter.
        first_batch = [{"i": CLASSIFY_BATCH_SIZE, "labels": ["Urgent"]}]
        second_batch = [{"i": 1, "labels": ["Newsletter"]}]

        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": [str(m.uuid) for m in messages]},
        )
        from workspace.ai.tasks.mail import classify_mail_messages

        with _labelling(first_batch, second_batch) as script:
            classify_mail_messages(str(ai_task.uuid))

        from workspace.mail.models import MailMessageLabel

        # Two batches -> two LLM calls.
        self.assertEqual(len(script.requests), 2)
        # 10th message (last of batch 1) labelled Urgent.
        self.assertTrue(
            MailMessageLabel.objects.filter(
                message=messages[CLASSIFY_BATCH_SIZE - 1], label=self.label_urgent
            ).exists()
        )
        # 11th message (first of batch 2) labelled Newsletter - proves the
        # per-batch index restart maps back to the right message.
        self.assertTrue(
            MailMessageLabel.objects.filter(
                message=messages[CLASSIFY_BATCH_SIZE], label=self.label_newsletter
            ).exists()
        )
        ai_task.refresh_from_db()
        self.assertEqual(ai_task.status, AITask.Status.COMPLETED)

    def test_preserves_input_uuid_order(self):
        """The LLM index (i=1, i=2, ...) must map to messages in the
        order the caller passed them in message_uuids. Without this,
        the DB returns rows in PK (uuid) order - random for v4 UUIDs -
        so labels end up on the wrong messages."""
        # Force a deterministic mismatch: msg_a's uuid sorts AFTER
        # msg_b's, but the caller passes [msg_a, msg_b].
        msg_a = MailMessage.objects.create(
            uuid="ffffffff-ffff-4fff-8fff-ffffffffffff",
            account=self.account,
            folder=self.folder,
            imap_uid=10,
            subject="Server down!",
            snippet="Production is down",
            from_name="Alert",
            from_email="alert@ops.com",
        )
        msg_b = MailMessage.objects.create(
            uuid="00000000-0000-4000-8000-000000000000",
            account=self.account,
            folder=self.folder,
            imap_uid=11,
            subject="Weekly digest",
            snippet="Here is your digest",
            from_name="News",
            from_email="news@co.com",
        )

        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": [str(msg_a.uuid), str(msg_b.uuid)]},
        )
        from workspace.ai.tasks.mail import classify_mail_messages

        with _labelling(
            [{"i": 1, "labels": ["Urgent"]}, {"i": 2, "labels": ["Newsletter"]}]
        ):
            classify_mail_messages(str(ai_task.uuid))

        from workspace.mail.models import MailMessageLabel

        self.assertTrue(
            MailMessageLabel.objects.filter(
                message=msg_a, label=self.label_urgent
            ).exists()
        )
        self.assertTrue(
            MailMessageLabel.objects.filter(
                message=msg_b, label=self.label_newsletter
            ).exists()
        )

    def test_unknown_label_names_ignored(self):
        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": [str(self.msg1.uuid)]},
        )
        from workspace.ai.tasks.mail import classify_mail_messages

        with _labelling([{"i": 1, "labels": ["Urgent", "NonExistent"]}]):
            classify_mail_messages(str(ai_task.uuid))

        self.assertEqual(self.msg1.message_labels.count(), 1)
        self.assertEqual(self.msg1.message_labels.first().label.name, "Urgent")

    def test_case_insensitive_label_matching(self):
        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": [str(self.msg1.uuid)]},
        )
        from workspace.ai.tasks.mail import classify_mail_messages

        with _labelling([{"i": 1, "labels": ["urgent", "NEWSLETTER"]}]):
            classify_mail_messages(str(ai_task.uuid))

        self.assertEqual(self.msg1.message_labels.count(), 2)

    def test_max_3_labels_per_message(self):
        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": [str(self.msg1.uuid)]},
        )
        from workspace.ai.tasks.mail import classify_mail_messages

        with _labelling(
            [
                {
                    "i": 1,
                    "labels": [
                        "Urgent",
                        "Action",
                        "FYI",
                        "Newsletter",
                        "Notification",
                    ],
                }
            ]
        ):
            classify_mail_messages(str(ai_task.uuid))

        self.assertEqual(self.msg1.message_labels.count(), 3)

    def test_malformed_json_fails_gracefully(self):
        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": [str(self.msg1.uuid)]},
        )
        from workspace.ai.tasks.mail import classify_mail_messages

        # An answer that is not the schema is asked for once more, then fails the task.
        with scripted(
            reply("not json", input_tokens=50, output_tokens=5), repeat=True
        ) as script:
            result = classify_mail_messages(str(ai_task.uuid))

        self.assertEqual(result["status"], "error")
        self.assertEqual(len(script.requests), 2)
        ai_task.refresh_from_db()
        self.assertEqual(ai_task.status, "failed")
        # The reason is the library's own sentence: only that there is one.
        self.assertTrue(ai_task.error)
        self.assertEqual(
            _stored_replies(ai_task),
            [("not json", None), ("not json", None)],
        )

    def test_reply_cut_at_the_token_cap_names_the_cap(self):
        from workspace.ai.tasks.mail import CLASSIFY_MAX_TOKENS, classify_mail_messages

        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": [str(self.msg1.uuid)]},
        )
        cut = ModelResponse(
            parts=[],
            usage=RequestUsage(input_tokens=50, output_tokens=CLASSIFY_MAX_TOKENS),
            finish_reason="length",
        )

        with scripted(cut):
            result = classify_mail_messages(str(ai_task.uuid))

        self.assertEqual(result["status"], "error")
        ai_task.refresh_from_db()
        self.assertEqual(ai_task.status, "failed")
        self.assertIn(str(CLASSIFY_MAX_TOKENS), ai_task.error)
        self.assertEqual(_stored_replies(ai_task), [("", "length")])

    def test_classify_leaves_room_for_reasoning(self):
        from workspace.ai.tasks.mail import CLASSIFY_MAX_TOKENS, classify_mail_messages

        ai_task = AITask.objects.create(
            owner=self.user,
            task_type=AITask.TaskType.CLASSIFY,
            input_data={"message_uuids": [str(self.msg1.uuid)]},
        )
        caps = []

        def answer(messages, info):
            caps.append(info.model_settings["max_tokens"])
            return reply(json.dumps({"results": []}))

        with patch(
            "workspace.ai.services.llm.get_model", return_value=FunctionModel(answer)
        ):
            classify_mail_messages(str(ai_task.uuid))

        (cap,) = caps
        self.assertGreaterEqual(cap, CLASSIFY_MAX_TOKENS)


class CleanLlmContentTests(TestCase):
    """Tests for clean_llm_content: stripping LLM artifacts."""

    def setUp(self):
        from workspace.ai.services.reply_text import clean_llm_content

        self.clean = clean_llm_content

    def test_strips_bracketed_timestamp_prefix(self):
        self.assertEqual(self.clean("[2026-04-10 20:07] Hello!"), "Hello!")

    def test_strips_unbracketed_timestamp_prefix(self):
        self.assertEqual(self.clean("2026-04-10 20:07 Hello!"), "Hello!")

    def test_strips_bracketed_timestamp_no_trailing_space(self):
        self.assertEqual(self.clean("[2026-04-10 20:07]Hello!"), "Hello!")

    def test_strips_tool_call_tags(self):
        self.assertEqual(self.clean("<tool_call>stuff</tool_call>"), "stuff")

    def test_strips_both_timestamp_and_tool_tags(self):
        self.assertEqual(
            self.clean("[2026-04-10 14:00] <tool_call>hi</tool_call>"),
            "hi",
        )

    def test_preserves_normal_content(self):
        self.assertEqual(self.clean("Just a normal reply."), "Just a normal reply.")

    def test_preserves_timestamps_mid_text(self):
        """Timestamps in the middle of a message should NOT be stripped."""
        text = "The meeting is at [2026-04-10 14:00] in room 3."
        self.assertEqual(self.clean(text), text)

    def test_empty_string(self):
        self.assertEqual(self.clean(""), "")

    def test_only_timestamp(self):
        self.assertEqual(self.clean("[2026-04-10 20:07]"), "")

    def test_only_timestamp_no_brackets(self):
        self.assertEqual(self.clean("2026-04-10 20:07"), "")

    def test_strips_assistant_images_marker_prefix(self):
        self.assertEqual(
            self.clean(
                "[Images sent by the assistant in the message above]\nVoila ton image !"
            ),
            "Voila ton image !",
        )

    def test_strips_assistant_images_marker_mid_text(self):
        """Dropping a standalone marker must not leave a blank line behind."""
        self.assertEqual(
            self.clean(
                "Voila ton image !\n\n"
                "[Images sent by the assistant in the message above]\n\n"
                "Tu en veux une autre ?"
            ),
            "Voila ton image !\n\nTu en veux une autre ?",
        )

    def test_strips_timestamp_exposed_by_marker_removal(self):
        """Both artifacts can leak at once; the timestamp prefix regex is
        anchored, so it only sees the message once the marker is gone."""
        self.assertEqual(
            self.clean(
                "[Images sent by the assistant in the message above]\n"
                "[2026-04-10 20:07] Hello!"
            ),
            "Hello!",
        )

    def test_strips_assistant_images_marker_case_insensitively(self):
        self.assertEqual(
            self.clean("[images sent by the ASSISTANT in the message above]\nHello"),
            "Hello",
        )

    def test_strips_every_assistant_images_marker(self):
        marker = "[Images sent by the assistant in the message above]"
        self.assertEqual(self.clean(f"{marker}\nA\n{marker}\nB"), "A\nB")

    def test_strips_a_leaked_history_header(self):
        """The header the model reads is one bracketed block; an imitation of it
        must go with the timestamp, not leave its prose in the reply."""
        self.assertEqual(
            self.clean("[2026-04-10 20:07 | Your reply.] Hello!"), "Hello!"
        )
        self.assertEqual(
            self.clean(
                "[2026-04-10 20:07 | Message from the user, Hana (@hana). "
                'In reply to your message: "Tea?".]Hi'
            ),
            "Hi",
        )

    def test_strips_image_caption_marker_mid_sentence(self):
        self.assertEqual(
            self.clean("Regarde [image: chat.png - A cat.] c'est mignon"),
            "Regarde c'est mignon",
        )

    def test_strips_image_caption_marker_on_its_own_line(self):
        self.assertEqual(
            self.clean("Et voila :\n[image: chat.png]\nMignon"), "Et voila :\nMignon"
        )

    def test_only_marker_becomes_empty(self):
        self.assertEqual(
            self.clean("[Images sent by the assistant in the message above]"), ""
        )

    def test_preserves_markdown_image_syntax(self):
        text = "![image: diagram](/static/a.png)"
        self.assertEqual(self.clean(text), text)


@override_settings(
    AI_API_KEY="test-key",
    AI_MODEL="gpt-4o-mini",
    AI_SMALL_MODEL="gpt-4o-mini",
)
class GenerateConversationTitleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="user", password="pass123")
        self.bot_user = User.objects.create_user(username="bot", password="pass123")
        BotProfile.objects.create(user=self.bot_user, system_prompt="Test bot.")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.user
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.bot_user
        )
        Message.objects.create(
            conversation=self.conversation,
            author=self.user,
            body="How do I write Django tests?",
        )

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()

    def test_generates_title_when_missing(self):
        from workspace.ai.tasks.chat import generate_conversation_title

        with scripted(reply(" Django testing help \n")):
            result = generate_conversation_title(str(self.conversation.uuid))

        self.assertEqual(result["status"], "ok")
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.title, "Django testing help")

    def test_skips_when_title_exists(self):
        self.conversation.title = "Existing title"
        self.conversation.save(update_fields=["title"])

        from workspace.ai.tasks.chat import generate_conversation_title

        with scripted() as script:
            result = generate_conversation_title(str(self.conversation.uuid))

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(script.requests, [])
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.title, "Existing title")

    def test_force_overwrites_existing_title(self):
        self.conversation.title = "Existing title"
        self.conversation.save(update_fields=["title"])

        from workspace.ai.tasks.chat import generate_conversation_title

        with scripted(reply("Fresh title")):
            result = generate_conversation_title(
                str(self.conversation.uuid), force=True
            )

        self.assertEqual(result["status"], "ok")
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.title, "Fresh title")

    def test_force_without_messages_skips(self):
        Message.objects.filter(conversation=self.conversation).delete()

        from workspace.ai.tasks.chat import generate_conversation_title

        with scripted() as script:
            result = generate_conversation_title(
                str(self.conversation.uuid), force=True
            )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(script.requests, [])
