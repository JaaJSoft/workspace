import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from django.conf import settings
from django.db import connections

from workspace.ai.metrics import AI_TOOL_CALLS, AI_TOOL_LOOP_STOPS, AI_TOOL_ROUNDS
from workspace.ai.services.call_order import set_call_position
from workspace.ai.services.llm import (
    build_tool_content,
    call_llm,
    extract_text_tool_calls,
    serialize_response,
    truncate_tool_result,
)
from workspace.ai.services.stream_steps import (
    notify_tool_step,
    notify_tool_step_done,
    step_recipients,
)
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# Tool handlers report a failure to the model as a plain string rather than an
# exception, so these prefixes are the only signal a call went wrong.
_FAILED_RESULT_PREFIXES = ("error:", "unknown tool:")


def _result_status(tool_result):
    text = tool_result if isinstance(tool_result, str) else ""
    return "error" if text.strip().lower().startswith(_FAILED_RESULT_PREFIXES) else "ok"


def _call_signature(tool_call):
    """Identity of a tool call: same tool, same arguments, same outcome.

    Arguments are re-serialized with sorted keys so a model that reorders
    them between two attempts is still recognized as repeating itself.
    """
    raw = tool_call.function.arguments or ""
    try:
        args = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"))
    except json.JSONDecodeError, TypeError:
        args = raw.strip()
    return f"{tool_call.function.name}:{args}"


@dataclass
class _PlannedCall:
    """One tool call of a round, and what came of it.

    Filled in three steps - planned on the main thread in call order, run
    (possibly off it), then recorded back in call order - so the model reads
    its results in the order it asked for them whatever order they land in.
    """

    tool_call: object
    metric_tool: str
    position: int = 0
    refusal: str | None = None
    result: object = None
    error: BaseException | None = None


def _batch_calls(tool_calls, concurrent_names, limit):
    """Split a round's calls into batches that may run in one dispatch.

    Consecutive independent calls share a batch, up to *limit*; every other
    call is a batch of its own, which keeps a write in the order the model
    asked for it and keeps every read before it strictly ahead of it.
    """
    batch = []
    for tool_call in tool_calls:
        if limit > 1 and tool_call.function.name in concurrent_names:
            batch.append(tool_call)
            if len(batch) == limit:
                yield batch
                batch = []
            continue
        if batch:
            yield batch
            batch = []
        yield [tool_call]
    if batch:
        yield batch


def _execute(entry, human_user, bot_user, conversation_id, tool_context):
    """Run one tool call and report its end to the conversation's streams.

    The report is sent from wherever the call ran, as soon as it returns,
    so the row of a quick call stops spinning without waiting for the slow
    one it was dispatched with.
    """
    from workspace.ai.tool_registry import tool_registry

    tool_call = entry.tool_call
    # Read by a tool that leaves an image for the caller to attach: appended
    # in completion order, they would reach the reply in a different order
    # than the model asked for them in.
    set_call_position(entry.position)
    try:
        return tool_registry.execute(
            tool_call,
            user=human_user,
            bot=bot_user,
            conversation_id=conversation_id,
            context=tool_context,
        )
    finally:
        if conversation_id:
            notify_tool_step_done(
                step_recipients(conversation_id, bot_user), conversation_id, tool_call
            )


def _execute_off_thread(*args):
    """Run one tool call in a pool thread and hand its connections back.

    Django opens a connection per thread on first query and nothing closes
    the ones a pool thread leaves behind, so a worker process would collect
    one for every parallel tool call it has ever run.
    """
    try:
        return _execute(*args)
    finally:
        connections.close_all()


def _run_batch(planned, human_user, bot_user, conversation_id, tool_context):
    """Execute the calls of one batch, storing each outcome on its entry.

    A single call runs inline: a thread buys nothing and costs a database
    connection. Exceptions are captured rather than raised so the caller
    can still report them in call order.
    """
    pending = [entry for entry in planned if entry.refusal is None]
    args = (human_user, bot_user, conversation_id, tool_context)
    if len(pending) <= 1:
        for entry in pending:
            try:
                entry.result = _execute(entry, *args)
            except Exception as exc:
                entry.error = exc
        return
    # One worker per call: everything dispatched starts immediately, so a
    # cancellation checked before dispatch cannot be outrun by a queued call.
    with ThreadPoolExecutor(max_workers=len(pending)) as pool:
        futures = [pool.submit(_execute_off_thread, entry, *args) for entry in pending]
    for entry, future in zip(pending, futures, strict=True):
        try:
            entry.result = future.result()
        except Exception as exc:
            entry.error = exc


def _repeat_notice(tool_call, limit):
    return (
        f"Not executed: this is the same call to {tool_call.function.name} with "
        f"the same arguments, {limit} times already in this reply. Repeating it "
        "returns what you already have. Read the earlier result again, try a "
        "different tool or different arguments, or answer with what you know."
    )


def run_tool_loop(
    messages,
    model,
    human_user,
    bot_user,
    conversation_id,
    is_cancelled=None,
    context=None,
):
    """Run the tool call loop and return (result, tool_context, rounds, tool_data).

    Calls the AI model, executes any tool calls it returns, and re-calls
    until we get a plain text response (capped at settings.AI_MAX_TOOL_ROUNDS
    rounds).

    A tool called more than settings.AI_MAX_IDENTICAL_TOOL_CALLS times with
    the same arguments is refused rather than run again, and a round made
    only of such refusals ends the loop on a final tool-less answer -
    reported through ``tool_context["repeat_loop_stopped"]``. A round cap
    exhausted on a pending tool call ends the same way, under
    ``round_cap_reached``; a run whose last round answered in text is a
    normal completion and flags nothing.

    Within a round, consecutive calls to tools the registry marks
    ``concurrent`` are dispatched together, settings.AI_TOOL_CONCURRENCY at
    a time, so a model that asks for a batch of pages waits for the slowest
    fetch of that batch rather than the sum of them all. Every other call
    runs alone, in the order the model asked for it, and results are
    recorded in that order whatever order they land in.

    *rounds* is a list
    of dicts capturing each LLM response and the tool executions that
    followed it, suitable for storage in ``AITask.raw_messages``.

    *tool_data* is a compact list of rounds suitable for persisting on
    ``Message.tool_data`` so that future history rebuilds can reconstruct
    the correct ``assistant(tool_calls) -> tool(result)`` message sequence.

    *is_cancelled* is an optional predicate read before every tool is
    dispatched. When it returns True the loop stops and reports it through
    ``tool_context["cancelled"]``, so a caller can tell an abandoned run
    from a finished one.

    *context* optionally seeds the tool context, letting the caller mark
    the kind of run for tools whose behavior depends on it (e.g. agent
    goal check-ins, where send_user_message is only valid because the
    final text is discarded). The same dict is returned as *tool_context*.
    """
    from workspace.ai.tool_registry import tool_registry

    tools = tool_registry.get_definitions()
    # A model can invent a tool name, and that name reaches a metric label:
    # anything the registry doesn't know is folded into one series.
    known_tools = {t["function"]["name"] for t in tools}
    result = call_llm(messages, model=model, tools=tools)

    tool_context = context if context is not None else {}
    rounds = []
    tool_data = []  # compact history for Message.tool_data
    max_tool_rounds = settings.AI_MAX_TOOL_ROUNDS
    max_identical_calls = settings.AI_MAX_IDENTICAL_TOOL_CALLS
    max_concurrency = settings.AI_TOOL_CONCURRENCY
    concurrent_names = tool_registry.concurrent_names()
    seen_calls = Counter()
    call_position = 0
    for _ in range(max_tool_rounds):
        # Fallback: parse tool calls from text if model didn't use native function calling
        if not result.get("tool_calls") and result.get("content"):
            raw_calls, remaining = extract_text_tool_calls(result["content"])
            if raw_calls:
                import types
                import uuid as _uuid

                result["content"] = remaining
                result["tool_calls"] = []
                for name, args_json in raw_calls:
                    call_id = f"call_{_uuid.uuid4().hex[:24]}"
                    tc = types.SimpleNamespace(
                        id=call_id,
                        type="function",
                        function=types.SimpleNamespace(name=name, arguments=args_json),
                    )
                    result["tool_calls"].append(tc)
                result["message"] = types.SimpleNamespace(
                    content=remaining or None,
                    tool_calls=result["tool_calls"],
                    role="assistant",
                )

        if not result.get("tool_calls"):
            rounds.append({"response": serialize_response(result)})
            break

        round_data = {
            "response": serialize_response(result),
            "tool_executions": [],
        }

        # Build tool_calls list for both the API message and tool_data persistence
        msg = result["message"]
        tc_list = (
            [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
            if msg.tool_calls
            else []
        )

        msg_dict = {"role": "assistant", "content": msg.content or ""}
        if tc_list:
            msg_dict["tool_calls"] = tc_list
        messages.append(msg_dict)

        td_round = {
            "assistant_content": msg.content or "",
            "thinking": result.get("thinking", ""),
            "tool_calls": tc_list,
            "results": [],
        }

        executed_in_round = 0
        for batch in _batch_calls(
            result["tool_calls"], concurrent_names, max_concurrency
        ):
            planned = []
            for tc in batch:
                # Read before dispatching, not after: past this point the tool
                # writes memories, schedules messages or bills an image, and
                # none of that should happen once the user has cancelled.
                if is_cancelled and is_cancelled():
                    tool_context["cancelled"] = True
                    break
                signature = _call_signature(tc)
                metric_tool = (
                    tc.function.name if tc.function.name in known_tools else "unknown"
                )
                seen_calls[signature] += 1
                call_position += 1
                entry = _PlannedCall(
                    tool_call=tc, metric_tool=metric_tool, position=call_position
                )
                planned.append(entry)
                if seen_calls[signature] > max_identical_calls:
                    # A model stuck re-issuing one call burns every remaining
                    # round on an answer it already has. Refusing the duplicate
                    # keeps the side effects single and tells it why nothing
                    # new came back.
                    logger.info(
                        "Blocked repeated tool call %s (%d times)",
                        scrub(tc.function.name),
                        seen_calls[signature],
                    )
                    entry.refusal = _repeat_notice(tc, max_identical_calls)
                    AI_TOOL_CALLS.labels(tool=metric_tool, status="repeat").inc()
                    continue
                executed_in_round += 1
                # Membership is re-read per tool, not snapshotted for the whole
                # generation: a member who leaves mid-run must stop receiving
                # progress from a conversation they are no longer in.
                if conversation_id:
                    notify_tool_step(
                        step_recipients(conversation_id, bot_user), conversation_id, tc
                    )

            _run_batch(planned, human_user, bot_user, conversation_id, tool_context)

            for entry in planned:
                tc = entry.tool_call
                if entry.error is not None:
                    AI_TOOL_CALLS.labels(tool=entry.metric_tool, status="error").inc()
                    raise entry.error
                if entry.refusal is not None:
                    tool_result = entry.refusal
                else:
                    tool_result = entry.result
                    AI_TOOL_CALLS.labels(
                        tool=entry.metric_tool, status=_result_status(tool_result)
                    ).inc()
                # Rides inside the residue of a trimmed result, so a stub the
                # model reads later still says which call produced it.
                call_hint = tool_registry.describe_call(
                    tc.function.name, tc.function.arguments
                )
                tool_content = build_tool_content(tool_result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_content,
                    }
                )
                round_data["tool_executions"].append(
                    {
                        "tool_call_id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                        "result": truncate_tool_result(
                            tool_result,
                            settings.AI_TOOL_RESULT_TASK_MAX_CHARS,
                            hint=call_hint,
                        ),
                    }
                )
                # Store a text-only version for history reconstruction
                td_result_content = tool_result
                if isinstance(tool_content, list):
                    # Multi-part content (e.g. image) - keep only the text part
                    td_result_content = next(
                        (
                            p["text"]
                            for p in tool_content
                            if isinstance(p, dict) and p.get("type") == "text"
                        ),
                        tool_result,
                    )
                td_round["results"].append(
                    {
                        "tool_call_id": tc.id,
                        "content": truncate_tool_result(
                            td_result_content,
                            settings.AI_TOOL_RESULT_STORE_MAX_CHARS,
                            hint=call_hint,
                        ),
                    }
                )

            if tool_context.get("cancelled"):
                break

        tool_data.append(td_round)
        rounds.append(round_data)
        # Re-read at the round boundary too, so a cancellation that lands
        # during the tools does not buy one more model call.
        if tool_context.get("cancelled") or (is_cancelled and is_cancelled()):
            tool_context["cancelled"] = True
            rounds[-1]["cancelled"] = True
            break
        if result["tool_calls"] and not executed_in_round:
            # Every call this round was a duplicate we refused, so the model is
            # circling rather than making progress. Let it answer from what it
            # already gathered instead of spending the remaining rounds.
            tool_context["repeat_loop_stopped"] = True
            rounds[-1]["repeat_loop_stopped"] = True
            AI_TOOL_LOOP_STOPS.labels(reason="repeat_loop").inc()
            result = call_llm(messages, model=model)
            rounds.append({"response": serialize_response(result)})
            break
        if tool_context.get("stop_after_round"):
            # A tool requested that we halt and wait for an external input
            # (e.g. a user click on an ask_user_question prompt). Don't
            # re-call the LLM until the user replies.
            rounds[-1]["terminated_by_tool"] = True
            break
        result = call_llm(messages, model=model, tools=tools)
    else:
        # Max rounds reached. The last response is another tool call that will
        # never run, so returning it hands the caller a reply with no text:
        # re-ask without tools to turn what was gathered into an answer.
        if result.get("tool_calls"):
            tool_context["round_cap_reached"] = True
            AI_TOOL_LOOP_STOPS.labels(reason="round_cap").inc()
            rounds.append(
                {"response": serialize_response(result), "round_cap_reached": True}
            )
            result = call_llm(messages, model=model)
        rounds.append({"response": serialize_response(result)})

    if not tool_context.get("cancelled"):
        # A cancelled run stopped for a reason unrelated to how the model
        # works, so counting it would flatten the distribution it measures.
        AI_TOOL_ROUNDS.labels(model=model or settings.AI_MODEL).observe(len(tool_data))

    return result, tool_context, rounds, tool_data or None


def retry_final_completion(messages, model):
    """Re-prompt the model for a final text completion without re-running
    any tools.

    Used by chat / scheduled tasks when the first :func:`run_tool_loop`
    returned an empty response: rerunning the full loop would
    re-execute every tool from scratch and trigger side effects twice
    (sending a message, writing data, ...). Calling ``call_llm``
    without ``tools`` forces the model to produce text instead, while
    the *messages* list - already mutated by the first loop with all
    tool calls and their results - keeps the conversation context.

    Returns ``(result, retry_rounds)`` so the caller can extend its
    ``rounds`` log; ``tool_context`` and ``tool_data``
    accumulated by the first pass are preserved on the caller side.
    """
    result = call_llm(messages, model=model)
    return result, [{"response": serialize_response(result)}]
