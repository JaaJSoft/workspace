"""Translating between the prompt builders' messages and Pydantic AI's.

The prompt builders write a conversation as Chat Completions dicts, and
the tasks read a reply as a :class:`~workspace.ai.harness.model.ModelResponse`.
Pydantic AI has its own message types on both sides, so a run crosses this
module twice: on the way in, and once per reply on the way out.
"""

from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.messages import ModelResponse as AgentResponse

from workspace.ai.harness.model import ModelResponse, ToolCall
from workspace.ai.services.llm import clean_llm_content, split_inline_thinking


def _user_content(content):
    """A user message's content, with its images as Pydantic AI carries them."""
    if not isinstance(content, list):
        return content or ""
    parts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            parts.append(part.get("text") or "")
        elif "image_url" in part:
            url = part["image_url"]["url"]
            parts.append(
                BinaryContent.from_data_uri(url)
                if url.startswith("data:")
                else ImageUrl(url=url)
            )
    return parts


def _text(content) -> str:
    if not isinstance(content, list):
        return content or ""
    return "\n".join(
        part.get("text") or ""
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def to_model_messages(messages: list[dict]) -> list[ModelMessage]:
    """Chat Completions dicts as a Pydantic AI message history.

    Everything the model is sent between two assistant turns is one
    request. A tool result names its tool, which the dicts only say on the
    call it answers.
    """
    history: list[ModelMessage] = []
    tool_names = {}
    for message in messages:
        role = message["role"]
        if role == "assistant":
            calls = message.get("tool_calls") or []
            parts = []
            if message.get("content") or not calls:
                parts.append(TextPart(_text(message.get("content"))))
            for call in calls:
                function = call["function"]
                tool_names[call["id"]] = function["name"]
                parts.append(
                    ToolCallPart(
                        function["name"],
                        function.get("arguments") or "{}",
                        tool_call_id=call["id"],
                    )
                )
            history.append(AgentResponse(parts=parts))
            continue
        if role == "system":
            part = SystemPromptPart(_text(message.get("content")))
        elif role == "tool":
            call_id = message["tool_call_id"]
            part = ToolReturnPart(
                tool_name=tool_names.get(call_id, ""),
                content=_text(message.get("content")),
                tool_call_id=call_id,
            )
        else:
            part = UserPromptPart(_user_content(message.get("content")))
        if history and isinstance(history[-1], ModelRequest):
            history[-1].parts.append(part)
        else:
            history.append(ModelRequest(parts=[part]))
    return history


def to_model_response(response: AgentResponse, *, duration=None) -> ModelResponse:
    """A Pydantic AI reply as the one the tasks and the record read.

    Reasoning the backend kept apart arrives as thinking parts; reasoning a
    model wrote between inline tags is still in the text, and is cut out of
    it here.
    """
    text = "".join(
        part.content for part in response.parts if isinstance(part, TextPart)
    )
    thinking = "\n\n".join(
        part.content.strip()
        for part in response.parts
        if isinstance(part, ThinkingPart) and part.content.strip()
    )
    inline_thinking, text = split_inline_thinking(text)
    usage = response.usage
    # A backend that reports no usage leaves both counts at zero.
    reported = bool(usage.input_tokens or usage.output_tokens)
    return ModelResponse(
        content=clean_llm_content(text),
        thinking=thinking or inline_thinking,
        tool_calls=[
            ToolCall(
                id=part.tool_call_id,
                name=part.tool_name,
                # The text as the model wrote it, where the backend sent text.
                arguments=part.args
                if isinstance(part.args, str)
                else part.args_as_json_str(),
            )
            for part in response.parts
            if isinstance(part, ToolCallPart)
        ],
        model=response.model_name or "",
        prompt_tokens=usage.input_tokens if reported else None,
        completion_tokens=usage.output_tokens if reported else None,
        duration=duration,
    )
