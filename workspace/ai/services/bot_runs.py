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

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

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

    - The tools are taken away, and the model left to answer from what it
      gathered, once *max_rounds* rounds have run or a whole round was
      refused duplicates.
    - A call already made *max_identical_calls* times with the same
      arguments is refused: a model stuck re-issuing one call burns its
      rounds on an answer it already has.
    - Each call is shown to the conversation's members while it runs.
      Membership is read per call, not once for the run: a member who
      leaves must stop receiving progress.
    - *is_cancelled* is read before every call, so a cancellation never
      starts a tool that writes memories, schedules messages or bills an
      image.
    """

    conversation_id: object
    bot_user: object
    max_rounds: int
    max_identical_calls: int
    is_cancelled: object = None
    stop: StopReason = StopReason.ANSWERED
    _seen: Counter = field(default_factory=Counter)
    # Whether each call of the round under way was refused.
    _round: list = field(default_factory=list)

    async def prepare_tools(self, ctx, tool_defs):
        refused, self._round = self._round, []
        if refused and all(refused):
            self.stop = StopReason.REPEAT_LOOP
        elif ctx.run_step > self.max_rounds:
            self.stop = StopReason.ROUND_CAP
        return tool_defs if self.stop is StopReason.ANSWERED else []

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        if await self.cancelled():
            # Ends the run once the calls already under way have returned.
            ctx.cancel()
            raise ToolFailed("Not executed: the reply was cancelled.")
        signature = _signature(call)
        self._seen[signature] += 1
        refused = self._seen[signature] > self.max_identical_calls
        self._round.append(refused)
        if refused:
            logger.info("Refused tool call %s", scrub(call.tool_name))
            AI_TOOL_CALLS.labels(tool=call.tool_name, status="repeat").inc()
            raise ToolFailed(
                f"Not executed: this is the same call to {call.tool_name} with the "
                f"same arguments, {self.max_identical_calls} times already in this "
                "reply. Repeating it returns what you already have. Read the "
                "earlier result again, try a different tool or different "
                "arguments, or answer with what you know."
            )
        await sync_to_async(self._announce)(call)
        status = "error"
        try:
            result = await handler(args)
            status = "ok"
            return result
        finally:
            AI_TOOL_CALLS.labels(tool=call.tool_name, status=status).inc()
            await sync_to_async(self._announce_done)(call)

    async def cancelled(self) -> bool:
        return bool(self.is_cancelled and await sync_to_async(self.is_cancelled)())

    def _announce(self, call):
        if self.conversation_id:
            notify_tool_step(
                step_recipients(self.conversation_id, self.bot_user),
                self.conversation_id,
                call,
            )

    def _announce_done(self, call):
        if self.conversation_id:
            notify_tool_step_done(
                step_recipients(self.conversation_id, self.bot_user),
                self.conversation_id,
                call,
            )


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
        return {"messages": llm.messages_for_storage(self.messages)}

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
        # A wrong call costs a round, and the rounds are what bounds a reply.
        retries={"tools": settings.AI_MAX_TOOL_ROUNDS, "output": 1},
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
                if await loop.cancelled():
                    return so_far, StopReason.CANCELLED, agent_run.usage
                # A tool asked to wait for the user: the results of its round
                # are not sent back to the model.
                if after_tools and deps.context.get("stop_after_round"):
                    return so_far, StopReason.AWAITING_USER, agent_run.usage
            return agent_run.all_messages(), loop.stop, agent_run.usage
    except RunCancelled as cancelled:
        return cancelled.all_messages(), StopReason.CANCELLED, RunUsage()
