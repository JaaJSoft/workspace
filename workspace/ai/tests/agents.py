"""Stand-ins for a backend and for a run, built on Pydantic AI's test models.

``scripted`` puts a model that replays written replies behind every agent
and one-shot prompt of the application. ``tool_context`` and ``run_tool``
call a tool without a model at all: the first to call a provider's method
directly, the second to go through the registered tool, its schema
included. ``BotRunCase`` runs a scripted bot over tools that record where,
when and in which language they ran.
"""

import threading
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase
from django.utils import translation
from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturn,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.usage import RequestUsage, RunUsage

from workspace.ai.prompts.base import prompt
from workspace.ai.services.bot_runs import run_bot
from workspace.ai.tool_registry import (
    ToolDeps,
    ToolError,
    ToolProvider,
    ToolRegistry,
    tool,
    tool_registry,
)


def call(name, arguments=None, call_id=None) -> ToolCallPart:
    """A tool call as a model makes it: *arguments* a dict, or JSON text."""
    return ToolCallPart(name, arguments if arguments is not None else {}, call_id)


def reply(
    text="", *calls, thinking="", input_tokens=0, output_tokens=0
) -> ModelResponse:
    """A reply of the model: its text, the tools it asks for, its reasoning.

    The name of the model that answered is the script's (``scripted(model=)``).
    """
    parts = []
    if thinking:
        parts.append(ThinkingPart(thinking))
    if text:
        parts.append(TextPart(text))
    parts.extend(calls)
    return ModelResponse(
        parts=parts,
        usage=RequestUsage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class Script:
    """Replays *replies* in order, and keeps what each request was sent.

    A reply may be a callable taking ``(messages, tools)``. With *repeat*,
    the last reply is given again for every request past the end, the way
    a stuck model keeps asking for the same call. ``requests`` holds
    ``(messages, tools)`` per request: the Pydantic AI messages sent, and
    the names of the tools on offer.
    """

    def __init__(self, *replies, repeat=False):
        self._replies = replies
        self._repeat = repeat
        self.requests = []

    def __call__(self, messages, info):
        tools = [tool.name for tool in info.function_tools]
        self.requests.append((list(messages), tools))
        index = len(self.requests) - 1
        if index >= len(self._replies):
            if not self._repeat:
                raise AssertionError("the script has no reply left for this request")
            index = len(self._replies) - 1
        answer = self._replies[index]
        return answer(messages, tools) if callable(answer) else answer

    @property
    def sent(self):
        """The conversation of the latest request."""
        return self.requests[-1][0]

    @property
    def tools(self):
        """The tools the latest request offered."""
        return self.requests[-1][1]


@contextmanager
def scripted(*replies, repeat=False, model="test-model"):
    """Answer every model request with *replies*; yields the :class:`Script`."""
    script = Script(*replies, repeat=repeat)
    # Like the backends the application talks to, it takes a system message
    # anywhere in the conversation: a script reads what they would be sent.
    answering = FunctionModel(
        script,
        model_name=model,
        profile=ModelProfile(
            supports_json_schema_output=True,
            supports_json_object_output=True,
            supports_inline_system_prompts=True,
        ),
    )
    with patch("workspace.ai.services.llm.get_model", return_value=answering):
        yield script


def tool_results(messages):
    """``(call id, content)`` of every tool result in *messages*."""
    return [
        (part.tool_call_id, part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def tool_context(user=None, bot=None, conversation_id=None, context=None):
    """The context a tool is handed, outside of any run."""
    return RunContext(
        deps=ToolDeps(
            user=user,
            bot=bot,
            conversation_id=conversation_id,
            context=context if context is not None else {},
        ),
        model=TestModel(),
        usage=RunUsage(),
        tool_call_id="call_test",
    )


def run_tool(name, arguments=None, *, registry=tool_registry, **deps):
    """Call the tool *name* of *registry* the way an agent does.

    *arguments* is a dict, or the JSON text a model would send: it is
    validated against the tool's schema first. Takes the keywords of
    :func:`tool_context`. A failure raises, as it does inside a run before
    the library turns it into what the model reads.
    """
    ctx = replace(tool_context(**deps), tool_name=name)
    toolset = registry.toolset()

    async def call_it():
        tool = (await toolset.get_tools(ctx))[name]
        validator = tool.args_validator
        if isinstance(arguments, str):
            validated = validator.validate_json(arguments)
        else:
            validated = validator.validate_python(arguments or {})
        return await toolset.call_tool(name, validated, ctx, tool)

    return async_to_sync(call_it)()


class UrlParams(BaseModel):
    url: str = "https://a.test"


class RecordingTools(ToolProvider):
    """Tools that record where, when and in which language they ran."""

    def __init__(self):
        self.spans = []
        self.threads = {}
        self.languages = {}
        self.in_flight = 0
        self.peak_in_flight = 0
        self.gate = None
        self._lock = threading.Lock()

    @property
    def executed(self):
        return [call_id for call_id, event in self.spans if event == "start"]

    def _run(self, ctx, result="ok"):
        call_id = ctx.tool_call_id
        with self._lock:
            self.spans.append((call_id, "start"))
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        self.threads[call_id] = threading.get_ident()
        self.languages[call_id] = translation.get_language()
        if self.gate is not None:
            self.gate(call_id)
        with self._lock:
            self.in_flight -= 1
            self.spans.append((call_id, "end"))
        return result

    @tool(detail_key="url", concurrent=True)
    def read_webpage(self, ctx: RunContext[ToolDeps], args: UrlParams) -> str:
        """Read a page."""
        return self._run(ctx, f"read {args.url}")

    @tool(detail_key="url")
    def write_note(self, ctx: RunContext[ToolDeps], args: UrlParams) -> str:
        """Write a note."""
        ctx.deps.context.setdefault("written", []).append(args.url)
        return self._run(ctx, "written")

    @tool()
    def ask_user(self, ctx: RunContext[ToolDeps]) -> str:
        """Ask the user something and wait."""
        ctx.deps.context["stop_after_round"] = True
        return self._run(ctx, "asked")

    @tool()
    def fail(self, ctx: RunContext[ToolDeps]) -> str:
        """Fail."""
        raise ToolError("the service is down", retryable=True)

    @tool()
    def explode(self, ctx: RunContext[ToolDeps]) -> str:
        """Raise a bug."""
        raise RuntimeError("tool exploded")

    @tool()
    def show_image(self, ctx: RunContext[ToolDeps]) -> ToolReturn:
        """Show an image."""
        image = BinaryContent(data=b"\x89PNG", media_type="image/png")
        return ToolReturn(return_value="a cat", content=[image])


class _Bot:
    """Stands for a bot profile: the model it runs on."""

    def get_model(self):
        return None


def read_call(call_id, url=None):
    return call("read_webpage", {"url": f"https://{url or call_id}.test"}, call_id)


def write_call(call_id, url="a"):
    return call("write_note", {"url": f"https://{url}.test"}, call_id)


class BotRunCase(TestCase):
    """Runs a scripted bot over :class:`RecordingTools` instead of the registered tools."""

    def setUp(self):
        self.tools = RecordingTools()
        registry = ToolRegistry()
        registry.register_provider(self.tools)
        patcher = patch("workspace.ai.services.bot_runs.tool_registry", registry)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_bot(self, *replies, repeat=False, **options):
        with scripted(*replies, repeat=repeat) as self.script:
            return run_bot(
                _Bot(),
                options.pop("user", None),
                None,
                options.pop("conversation_id", None),
                prompt("be helpful", "go"),
                **options,
            )
