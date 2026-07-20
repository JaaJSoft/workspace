import html
import json
import logging

from workspace.ai.services.llm import (
    build_tool_content,
    call_llm,
    extract_text_tool_calls,
    serialize_response,
    truncate_tool_result,
)

logger = logging.getLogger(__name__)


def _track_tool_usage(tool_call, tool_result, used_tools):
    """Extract a human-readable detail from a successful tool call."""
    from workspace.ai.tool_registry import tool_registry

    name = tool_call.function.name
    try:
        args = json.loads(tool_call.function.arguments)
    except (json.JSONDecodeError, AttributeError):
        args = {}
    used_tools.append((name, tool_registry.get_detail(name, args)))


def render_tool_badges(used_tools):
    """Render HTML badges for tools used during response generation."""
    from workspace.ai.tool_registry import tool_registry

    grouped = {}
    for name, detail in used_tools:
        grouped.setdefault(name, [])
        if detail:
            grouped[name].append(detail)

    parts = []
    for name, details in grouped.items():
        cfg = tool_registry.get_badge(name)
        icon = cfg["icon"]
        label = cfg["label"]
        # `label`/`details` come from the registry/get_detail() with model- or
        # user-driven values. Escape before interpolating into body_html.
        if details:
            details_display = " &bull; ".join(html.escape(d) for d in details)
            parts.append(f"<span>{icon}</span> {html.escape(label)}: {details_display}")
        else:
            parts.append(f"<span>{icon}</span> {html.escape(label)}")

    # Single tool or short badges: inline. Multiple tools: one per line.
    if len(parts) <= 2:
        badges_html = ' <span class="opacity-30">|</span> '.join(parts)
        return (
            f'\n<div class="mt-2 text-xs text-base-content/40 flex items-center gap-1 flex-wrap">'
            f"{badges_html}"
            f"</div>"
        )

    badges_html = "".join(
        f'<div class="flex items-center gap-1">{p}</div>' for p in parts
    )
    return (
        f'\n<div class="mt-2 text-xs text-base-content/40 flex flex-col gap-0.5">'
        f"{badges_html}"
        f"</div>"
    )


def run_tool_loop(messages, model, human_user, bot_user, conversation_id):
    """Run the tool call loop and return (result, used_tools, tool_context, rounds, tool_data).

    Calls the AI model, executes any tool calls it returns, and re-calls
    until we get a plain text response (max 5 rounds).  *rounds* is a list
    of dicts capturing each LLM response and the tool executions that
    followed it, suitable for storage in ``AITask.raw_messages``.

    *tool_data* is a compact list of rounds suitable for persisting on
    ``Message.tool_data`` so that future history rebuilds can reconstruct
    the correct ``assistant(tool_calls) -> tool(result)`` message sequence.
    """
    from workspace.ai.tool_registry import tool_registry

    tools = tool_registry.get_definitions()
    result = call_llm(messages, model=model, tools=tools)

    used_tools = []
    tool_context = {}
    rounds = []
    tool_data = []  # compact history for Message.tool_data
    max_tool_rounds = 5
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
            "tool_calls": tc_list,
            "results": [],
        }

        for tc in result["tool_calls"]:
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
            if "Error" not in tool_result and "Unknown tool" not in tool_result:
                _track_tool_usage(tc, tool_result, used_tools)
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

    return result, used_tools, tool_context, rounds, tool_data or None


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
    ``rounds`` log; ``tool_context``, ``used_tools`` and ``tool_data``
    accumulated by the first pass are preserved on the caller side.
    """
    result = call_llm(messages, model=model)
    return result, [{"response": serialize_response(result)}]
