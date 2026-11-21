"""A bot reply on the Pydantic AI runtime, from the task down to the wire."""

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx2
import openai
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from pydantic_ai import direct
from pydantic_ai.messages import ModelRequest
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider

from workspace.ai.harness.pydantic_runtime.model import model_settings, open_model
from workspace.ai.harness.pydantic_runtime.runner import PydanticAIRunner
from workspace.ai.harness.runner import AgentRunner
from workspace.ai.models import AITask, BotProfile, UserMemory
from workspace.ai.services.bot_runs import build_bot_runner
from workspace.ai.tasks.chat import generate_chat_response
from workspace.chat.models import Conversation, ConversationMember, Message

from .chat_tasks import queue_chat_task
from .harness import ScriptedModel, call, reply, tool_reply
from .pydantic_runtime import scripted

User = get_user_model()


def _bot_conversation(test, message):
    test.user = User.objects.create_user(username="user", password="pass123")
    test.bot_user = User.objects.create_user(username="bot", password="pass123")
    test.bot_profile = BotProfile.objects.create(
        user=test.bot_user, system_prompt="You are a test bot.", is_public=True
    )
    # Titled, so that a reply does not go on to ask a backend for a title.
    test.conversation = Conversation.objects.create(
        kind=Conversation.Kind.DM, created_by=test.user, title="Test conversation"
    )
    ConversationMember.objects.create(conversation=test.conversation, user=test.user)
    ConversationMember.objects.create(
        conversation=test.conversation, user=test.bot_user
    )
    test.message = Message.objects.create(
        conversation=test.conversation, author=test.user, body=message
    )


class RuntimeChoiceTests(TestCase):
    def setUp(self):
        _bot_conversation(self, "hello")

    def tearDown(self):
        cache.clear()

    def _runner(self):
        return build_bot_runner(
            self.bot_profile, self.user, self.bot_user, self.conversation.pk
        )

    def test_the_native_loop_runs_a_reply_by_default(self):
        self.assertIsInstance(self._runner(), AgentRunner)

    @override_settings(AI_AGENT_RUNTIME="pydantic_ai")
    def test_the_setting_puts_the_reply_on_pydantic_ai(self):
        self.assertIsInstance(self._runner(), PydanticAIRunner)


@override_settings(
    AI_API_KEY="test-key",
    AI_AGENT_RUNTIME="pydantic_ai",
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class ChatResponseTests(TestCase):
    """The chat task, unchanged, with the other runtime under it."""

    def setUp(self):
        _bot_conversation(self, "My name is Pierre")

    def tearDown(self):
        cache.clear()

    def _answer_with(self, model):
        function_model = scripted(model)

        @asynccontextmanager
        async def opened(name):
            yield function_model

        return patch("workspace.ai.harness.pydantic_runtime.model.open_model", opened)

    def test_a_tool_writes_through_the_tasks_own_connection_then_the_bot_answers(self):
        model = ScriptedModel(
            [
                tool_reply(
                    call(
                        "call_abc",
                        name="save_memory",
                        arguments='{"key": "name", "content": "Pierre"}',
                    ),
                    prompt_tokens=20,
                    completion_tokens=10,
                ),
                reply("Got it, Pierre!", prompt_tokens=30, completion_tokens=8),
            ]
        )

        with self._answer_with(model):
            result = generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        self.assertEqual(result["status"], "ok")
        # Written inside this test's transaction: the tool ran on the thread
        # that started the run, not on one with a connection of its own.
        memory = UserMemory.objects.get(user=self.user, bot=self.bot_user, key="name")
        self.assertEqual(memory.content, "Pierre")
        bot_message = Message.objects.get(author=self.bot_user)
        self.assertEqual(bot_message.body, "Got it, Pierre!")
        (round_,) = bot_message.tool_data
        self.assertEqual(round_["tool_calls"][0]["function"]["name"], "save_memory")
        self.assertEqual(round_["results"][0]["tool_call_id"], "call_abc")
        task = AITask.objects.get(chat_message=bot_message)
        self.assertEqual(task.status, AITask.Status.COMPLETED)
        self.assertEqual((task.prompt_tokens, task.completion_tokens), (50, 18))
        self.assertEqual(len(task.raw_messages["rounds"]), 2)

    def test_the_model_is_offered_the_registered_tools_and_the_chat_prompt(self):
        model = ScriptedModel([reply("Hello")])

        with self._answer_with(model):
            generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        messages, tools = model.requests[0]
        self.assertIn("save_memory", tools)
        (request,) = messages
        self.assertIsInstance(request, ModelRequest)
        self.assertIn("You are a test bot.", request.parts[0].content)
        self.assertIn("My name is Pierre", [part.content for part in request.parts])

    def test_an_empty_reply_is_asked_again_once_without_tools(self):
        model = ScriptedModel([reply(""), reply("Here I am")])

        with self._answer_with(model):
            result = generate_chat_response(
                queue_chat_task(self.conversation, self.message, self.bot_user)
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(Message.objects.get(author=self.bot_user).body, "Here I am")
        self.assertIsNone(model.last_tools)


def _completion(request):
    body = json.loads(request.content)
    return httpx2.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "created": 1,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "hello"},
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        },
    )


@override_settings(AI_API_KEY="test-key", AI_MAX_TOKENS=321, AI_MAX_RETRIES=0)
class OpenModelTests(SimpleTestCase):
    def _request(self):
        """One request through ``open_model``, as the backend received it."""
        sent = []

        def backend(request):
            sent.append(request)
            return _completion(request)

        def client(**options):
            transport = httpx2.MockTransport(backend)
            return real_client(
                **options, http_client=httpx2.AsyncClient(transport=transport)
            )

        async def ask():
            async with open_model(None) as model:
                await direct.model_request(
                    model,
                    [ModelRequest.user_text_prompt("hi")],
                    model_settings=model_settings(),
                )
                return model

        real_client = openai.AsyncOpenAI
        with patch("openai.AsyncOpenAI", client):
            model = async_to_sync(ask)()
        (request,) = sent
        return model, request, json.loads(request.content)

    @override_settings(AI_BASE_URL=None, AI_MODEL="gpt-5")
    def test_openai_is_sent_the_reply_cap_under_the_name_it_accepts(self):
        # Its reasoning models, the default one included, refuse max_tokens.
        model, request, body = self._request()

        self.assertIsInstance(model.provider, OpenAIProvider)
        self.assertEqual(request.url.host, "api.openai.com")
        self.assertEqual(body["model"], "gpt-5")
        self.assertEqual(body["max_completion_tokens"], 321)
        self.assertNotIn("max_tokens", body)

    @override_settings(AI_BASE_URL="http://ollama.test:11434/v1/", AI_MODEL="gemma")
    def test_another_base_url_is_a_service_that_only_speaks_the_protocol(self):
        model, request, body = self._request()

        self.assertIsInstance(model.provider, OpenAICompatibleProvider)
        self.assertEqual(request.url.host, "ollama.test")
        self.assertEqual(request.headers["authorization"], "Bearer test-key")
        self.assertEqual(body["model"], "gemma")

    @override_settings(AI_API_KEY="")
    def test_no_key_is_no_backend(self):
        async def ask():
            async with open_model(None):
                pass

        with self.assertRaisesMessage(RuntimeError, "AI is not configured"):
            async_to_sync(ask)()
