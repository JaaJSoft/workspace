import logging

from django.conf import settings

from workspace.ai.services.llm import (
    build_tool_content,
    call_llm,
    extract_text_tool_calls,
    serialize_response,
    truncate_tool_result,
)
from workspace.ai.services.stream_steps import notify_tool_step, step_recipients

logger = logging.getLogger(__name__)


def run_tool_loop(
    messages, model, human_user, bot_user, conversation_id, is_cancelled=None
):
    """Run the tool call loop and return (result, tool_context, rounds, tool_data).

    Calls the AI model, executes any tool calls it returns, and re-calls
    until we get a plain text response (capped at settings.AI_MAX_TOOL_ROUNDS
    rounds).  *rounds* is a list
    of dicts capturing each LLM response and the tool executions that
    followed it, suitable for storage in ``AITask.raw_messages``.

    *tool_data* is a compact list of rounds suitable for persisting on
    ``Message.tool_data`` so that future history rebuilds can reconstruct
    the correct ``assistant(tool_calls) -> tool(result)`` message sequence.

    *is_cancelled* is an optional predicate read before every tool
    execution. When it returns True the loop stops and reports it through
    ``tool_context["cancelled"]``, so a caller can tell an abandoned run
    from a finished one.
    """
    from workspace.ai.tool_registry import tool_registry

    tools = tool_registry.get_definitions()
    result = call_llm(messages, model=model, tools=tools)

    tool_context = {}
    rounds = []
    tool_data = []  # compact history for Message.tool_data
    max_tool_rounds = settings.AI_MAX_TOOL_ROUNDS
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

        for tc in result["tool_calls"]:
            # Read before executing, not after the loop: past this point the
            # tool writes memories, schedules messages or bills an image, and
            # none of that should happen once the user has cancelled.
            if is_cancelled and is_cancelled():
                tool_context["cancelled"] = True
                break
            # Membership is re-read per tool, not snapshotted for the whole
            # generation: a member who leaves mid-run must stop receiving
            # progress from a conversation they are no longer in.
            if conversation_id:
                notify_tool_step(
                    step_recipients(conversation_id, bot_user), conversation_id, tc
                )
            tool_result = tool_registry.execute(
                tc,
                user=human_user,
                bot=bot_user,
                conversation_id=conversation_id,
                context=tool_context,
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
                    "result": truncate_tool_result(tool_result),
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
                    "content": truncate_tool_result(td_result_content),
                }
            )

        tool_data.append(td_round)
        rounds.append(round_data)
        # Re-read at the round boundary too, so a cancellation that lands
        # during the tools does not buy one more model call.
        if tool_context.get("cancelled") or (is_cancelled and is_cancelled()):
            tool_context["cancelled"] = True
            rounds[-1]["cancelled"] = True
            break
        if tool_context.get("stop_after_round"):
            # A tool requested that we halt and wait for an external input
            # (e.g. a user click on an ask_user_question prompt). Don't
            # re-call the LLM until the user replies.
            rounds[-1]["terminated_by_tool"] = True
            break
        result = call_llm(messages, model=model, tools=tools)
    else:
        # Max rounds reached - capture the final response
        rounds.append({"response": serialize_response(result)})

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
