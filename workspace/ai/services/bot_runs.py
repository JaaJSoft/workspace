"""One bot reply, run on a Pydantic AI agent.

The three ways a bot comes to answer - a message in the chat, a scheduled
message, an agent goal check-in - run the same agent with the same tools,
the same limits and the same watchers. This is the one place it is put
together, so a task holds a :class:`BotRun` and nothing of how it was made.

The agent's loop is the library's: it asks the model, runs the tools the
reply asks for, and asks again. What a reply adds around it is
:class:`BotLoop`, a capability: when the tools are taken away, which calls
are refused, who is told a tool is running, and when the user's
cancellation stops everything.
"""

import asyncio
import json
import logging
from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass, field
from enum import Enum
from itertools import groupby

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import RunCancelled, ToolFailed
from pydantic_ai.messages import (
    ERROR_OUTCOMES,
    ModelMessage,
    ModelResponse,
    ToolReturnPart,
)
from pydantic_ai.usage import RunUsage, UsageLimits

from workspace.ai.metrics import AI_TOOL_CALLS, AI_TOOL_LOOP_STOPS, AI_TOOL_ROUNDS
from workspace.ai.services import llm
from workspace.ai.services.reply_text import truncate_middle
from workspace.ai.services.stream_steps import (
    notify_tool_step,
    notify_tool_step_done,
    step_recipients,
)
from workspace.ai.tool_registry import ToolDeps, tool_registry
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


class StopReason(Enum):
    ANSWERED = "answered"
    # A tool asked the run to halt and wait for the user (a question, a
    # confirmation): the reply is what the model said with that call, and
    # the conversation resumes on the user's next message.
    AWAITING_USER = "awaiting_user"
    CANCELLED = "cancelled"
    # Every call of a round was a refused duplicate: the model was circling.
    REPEAT_LOOP = "repeat_loop"
    ROUND_CAP = "round_cap"


# The library fails a run whose model keeps calling a tool wrongly. Here a
# wrong call costs a round like any other, and the rounds and the repeat
# guard are what bound a reply.
_NO_RETRY_BUDGET = 1_000_000


def produced_media(tool_context) -> bool:
    """Whether tools left media for the reply to carry.

    A reply that is only an image or only a voice message has an empty body
    on purpose, and must not be mistaken for the model answering nothing.
    """
    return bool(tool_context.get("images") or tool_context.get("voices"))


def _signature(call) -> str:
    """Identity of a call: same tool, same arguments, in whatever key order."""
    try:
        arguments = json.dumps(
            call.args_as_dict(), sort_keys=True, separators=(",", ":")
        )
    except ValueError, TypeError:
        arguments = str(call.args).strip()
    return f"{call.tool_name}:{arguments}"


@dataclass
class BotLoop(AbstractCapability[ToolDeps]):
    """What a bot reply adds around the agent's loop.

    - A reply gets *max_rounds* rounds of tools. The calls of a round past
      them are refused, and the tools taken away for the answer.
    - A call already made *max_identical_calls* times with the same
      arguments is refused: a model stuck re-issuing one call burns its
      rounds on an answer it already has. A round of nothing but such
      repeats takes the tools away too.
    - Each call is shown to the conversation's members while it runs.
      Membership is read per call, not once for the run: a member who
      leaves must stop receiving progress.
    - *is_cancelled* is read as each call gets its turn, so a cancellation
      never starts a tool that writes memories, schedules messages or bills
      an image.

    A round is settled when the reply that asks for it arrives, from every
    call it holds: the library answers by itself a call to a tool that does
    not exist or with arguments its schema refuses, and such a call never
    reaches :meth:`wrap_tool_execute`.
    """

    conversation_id: object
    bot_user: object
    max_rounds: int
    max_identical_calls: int
    concurrency: int
    is_cancelled: object = None
    stop: StopReason = StopReason.ANSWERED
    _rounds: int = 0
    _seen: Counter = field(default_factory=Counter)
    # What the model is told in place of a result, per refused call of the
    # round under way.
    _refusals: dict = field(default_factory=dict)
    # Calls of that round that got as far as their tool.
    _reached: set = field(default_factory=set)
    _known: frozenset = frozenset()
    _slots: asyncio.Semaphore | None = None

    async def prepare_tools(self, ctx, tool_defs):
        return tool_defs if self.stop is StopReason.ANSWERED else []

    async def after_model_request(self, ctx, *, request_context, response):
        if response.tool_calls:
            offered = request_context.model_request_parameters.function_tools
            self._settle_round(ctx.deps, response.tool_calls, offered)
        return response

    def _settle_round(self, deps, calls, offered):
        self._rounds += 1
        self._known = frozenset(tool.name for tool in offered)
        self._refusals, self._reached = {}, set()
        if self._rounds > self.max_rounds:
            self.stop = StopReason.ROUND_CAP
            self._refusals = dict.fromkeys(
                (call.tool_call_id for call in calls),
                f"Not executed: this reply has used its {self.max_rounds} rounds "
                "of tools. Answer now with what you have gathered.",
            )
        else:
            for call in calls:
                signature = _signature(call)
                self._seen[signature] += 1
                if self._seen[signature] > self.max_identical_calls:
                    self._refuse_repeat(call)
            if len(self._refusals) == len(calls):
                self.stop = StopReason.REPEAT_LOOP
        deps.parallel_calls = self._parallel(calls, offered)

    def _refuse_repeat(self, call):
        logger.info("Refused tool call %s", scrub(call.tool_name))
        AI_TOOL_CALLS.labels(tool=self._label(call), status="repeat").inc()
        self._refusals[call.tool_call_id] = (
            f"Not executed: this is the same call to {call.tool_name} with the "
            f"same arguments, {self.max_identical_calls} times already in this "
            "reply. Repeating it returns what you already have. Read the "
            "earlier result again, try a different tool or different "
            "arguments, or answer with what you know."
        )

    def _parallel(self, calls, offered) -> frozenset:
        """The calls of a round that run beside another, on pool threads.

        A read with no neighbour to run beside stays on the task's thread:
        a thread and a database connection of its own would buy it nothing.
        """
        if self.concurrency <= 1:
            return frozenset()
        concurrent = {tool.name for tool in offered if not tool.sequential}
        together = set()
        for shares, group in groupby(calls, key=lambda c: c.tool_name in concurrent):
            pending = [
                c.tool_call_id for c in group if c.tool_call_id not in self._refusals
            ]
            if shares and len(pending) > 1:
                together.update(pending)
        return frozenset(together)

    def _label(self, call) -> str:
        # A model can invent any name, and a name is a metric label.
        return call.tool_name if call.tool_name in self._known else "unknown"

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        self._reached.add(call.tool_call_id)
        if refusal := self._refusals.get(call.tool_call_id):
            raise ToolFailed(refusal)
        if self._slots is None:
            self._slots = asyncio.Semaphore(self.concurrency)
        pooled = call.tool_call_id in ctx.deps.parallel_calls
        async with self._slots if pooled else nullcontext():
            # Read once the call has its slot, not when it joined the queue:
            # the ones before it may have run for minutes.
            if await self.cancelled():
                # Ends the run once the calls already under way have returned.
                ctx.cancel()
                raise ToolFailed("Not executed: the reply was cancelled.")
            await sync_to_async(self._announce)(call)
            status = "error"
            try:
                result = await handler(args)
                status = "ok"
                return result
            finally:
                AI_TOOL_CALLS.labels(tool=call.tool_name, status=status).inc()
                await sync_to_async(self._announce_done)(call)

    def count_unreached(self, results):
        """Count the calls of the round the library answered by itself.

        *results* is the request it built from the round. A tool that does
        not exist, arguments the schema refuses: those are failed calls like
        any other, and a model that starts making them has to show.
        """
        for part in results.parts:
            if (
                isinstance(part, ToolReturnPart)
                and part.outcome in ERROR_OUTCOMES
                and part.tool_call_id not in self._reached
                and part.tool_call_id not in self._refusals
            ):
                label = part.tool_name if part.tool_name in self._known else "unknown"
                AI_TOOL_CALLS.labels(tool=label, status="error").inc()

    async def cancelled(self) -> bool:
        return bool(self.is_cancelled and await sync_to_async(self.is_cancelled)())

    def _announce(self, call):
        self._show(notify_tool_step, call)

    def _announce_done(self, call):
        self._show(notify_tool_step_done, call)

    def _show(self, notify, call):
        # Watching only: by the time a step cannot be shown the tool may
        # have sent an email, and the reply must not fail for it.
        if not self.conversation_id:
            return
        try:
            recipients = step_recipients(self.conversation_id, self.bot_user)
            notify(recipients, self.conversation_id, call)
        except Exception:
            logger.exception("Could not show a tool step")


def tool_rounds(messages: list[ModelMessage]) -> list[dict]:
    """The tool rounds of a run, as ``Message.tool_data`` keeps them.

    One entry per reply that called tools: what the model said, its
    reasoning, the calls and what each came back with, cut to
    ``AI_TOOL_RESULT_STORE_MAX_CHARS``. The chat's timeline is drawn from
    it, and the next turn's history rebuilt from it.
    """
    rounds = []
    for message in messages:
        if isinstance(message, ModelResponse):
            if message.tool_calls:
                rounds.append(
                    {
                        "assistant_content": message.text or "",
                        "thinking": message.thinking or "",
                        "tool_calls": [
                            {
                                "id": call.tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": call.tool_name,
                                    "arguments": call.args
                                    if isinstance(call.args, str)
                                    else call.args_as_json_str(),
                                },
                            }
                            for call in message.tool_calls
                        ],
                        "results": [],
                    }
                )
            continue
        if not rounds:
            continue
        calls = {call["id"]: call["function"] for call in rounds[-1]["tool_calls"]}
        for part in message.parts:
            if not isinstance(part, ToolReturnPart) or part.tool_call_id not in calls:
                continue
            call = calls[part.tool_call_id]
            result = {
                "tool_call_id": part.tool_call_id,
                "content": truncate_middle(
                    part.model_response_str(wrap_if_error=False),
                    settings.AI_TOOL_RESULT_STORE_MAX_CHARS,
                    tool_registry.describe_call(call["name"], call["arguments"]),
                ),
            }
            if part.outcome in ERROR_OUTCOMES:
                result["outcome"] = "failed"
            rounds[-1]["results"].append(result)
    return rounds


@dataclass
class BotRun:
    """What a run came to.

    *messages* is the whole conversation, the one the run was given and
    what it added; *new_messages* only the latter. *response* is the reply
    the run ended on, and *output* its text.
    """

    output: str
    response: ModelResponse
    messages: list
    new_messages: list
    context: dict
    stop: StopReason
    usage: RunUsage
    # Seconds the backend took over every reply, and over the last one.
    seconds: float | None = None
    answer_seconds: float | None = None

    @property
    def thinking(self) -> str:
        return (self.response.thinking or "").strip()

    @property
    def tool_data(self) -> list | None:
        return tool_rounds(self.new_messages) or None

    @property
    def raw_messages(self) -> dict:
        return {
            "messages": llm.messages_for_storage(self.messages),
            "stop": self.stop.value,
        }

    def task_fields(self) -> dict:
        """What the run cost, as the AITask columns that record it."""
        return {
            **llm.usage_fields(self.response, self.usage),
            "generation_seconds": self.seconds,
            "answer_tokens": self.response.usage.output_tokens or None,
            "answer_seconds": self.answer_seconds,
        }


def run_bot(
    bot_profile,
    human_user,
    bot_user,
    conversation_id,
    messages: list[ModelMessage],
    *,
    is_cancelled=None,
    context=None,
    expects_answer=True,
) -> BotRun:
    """Run *bot_profile*'s reply to *messages* in *conversation_id*.

    *context* seeds the dict tools write into, letting the caller mark the
    kind of run for tools whose behavior depends on it (an agent goal
    check-in, where send_user_message is only valid because the final text
    is discarded). With *expects_answer*, a reply that says nothing and
    leaves no media is sent back to the model once, then fails the run.
    """
    deps = ToolDeps(
        user=human_user,
        bot=bot_user,
        conversation_id=conversation_id,
        context=context if context is not None else {},
    )
    loop = BotLoop(
        conversation_id=conversation_id,
        bot_user=bot_user,
        max_rounds=settings.AI_MAX_TOOL_ROUNDS,
        max_identical_calls=settings.AI_MAX_IDENTICAL_TOOL_CALLS,
        concurrency=settings.AI_TOOL_CONCURRENCY,
        is_cancelled=is_cancelled,
    )
    timer = llm.RequestMetrics()
    model = llm.get_model(bot_profile.get_model())
    agent = Agent(
        model,
        deps_type=ToolDeps,
        # A reply may be media alone: no text is then a valid answer.
        output_type=str | None,
        toolsets=[tool_registry.toolset()],
        model_settings=llm.model_settings(),
        retries={"tools": _NO_RETRY_BUDGET, "output": 1},
        capabilities=[llm.ReplyHygiene(), timer, loop],
    )

    @agent.output_validator
    def answered(ctx: RunContext[ToolDeps], output: str | None) -> str | None:
        if expects_answer and not output and not produced_media(ctx.deps.context):
            raise ModelRetry("Your reply was empty. Answer the user in plain text.")
        return output

    all_messages, stop, usage = async_to_sync(_iterate)(agent, deps, loop, messages)
    new_messages = all_messages[len(messages) :]
    response = next(
        (m for m in reversed(new_messages) if isinstance(m, ModelResponse)),
        ModelResponse(parts=[]),
    )
    if stop in (StopReason.ROUND_CAP, StopReason.REPEAT_LOOP):
        AI_TOOL_LOOP_STOPS.labels(reason=stop.value).inc()
    if stop is not StopReason.CANCELLED:
        # A cancelled run stopped for a reason unrelated to how the model
        # works, so counting it would flatten the distribution it measures.
        AI_TOOL_ROUNDS.labels(model=model.model_name).observe(
            len(tool_rounds(new_messages))
        )
    return BotRun(
        output=response.text or "",
        response=response,
        messages=all_messages,
        new_messages=new_messages,
        context=deps.context,
        stop=stop,
        usage=usage,
        seconds=timer.seconds,
        answer_seconds=timer.last_seconds,
    )


async def _iterate(agent, deps, loop, messages):
    """Step the agent to the end of the reply: its messages, why it stopped, its usage."""
    try:
        async with agent.iter(
            message_history=messages,
            deps=deps,
            # The rounds bound a reply, not a count of requests.
            usage_limits=UsageLimits(request_limit=None),
        ) as agent_run:
            # Each node is handed over before it runs: a model request not
            # worth making is simply not made.
            async for node in agent_run:
                if not Agent.is_model_request_node(node):
                    continue
                after_tools = any(
                    isinstance(part, ToolReturnPart) for part in node.request.parts
                )
                so_far = agent_run.all_messages()
                if after_tools:
                    so_far = [*so_far, node.request]
                    loop.count_unreached(node.request)
                if await loop.cancelled():
                    return so_far, StopReason.CANCELLED, agent_run.usage
                # A tool asked to wait for the user: the results of its round
                # are not sent back to the model.
                if after_tools and deps.context.get("stop_after_round"):
                    return so_far, StopReason.AWAITING_USER, agent_run.usage
            return agent_run.all_messages(), loop.stop, agent_run.usage
    except RunCancelled as cancelled:
        return cancelled.all_messages(), StopReason.CANCELLED, RunUsage()
