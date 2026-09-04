"""What crosses the boundary between the tool loop and the model.

The runner reads a model reply as a :class:`ModelResponse` and asks for
tools as :class:`ToolCall` values, whatever the backend answered with:
native function calling, calls written out as JSON in the text, reasoning
in a field or between inline tags. Absorbing those differences here is what
lets the loop itself say nothing about backends.
"""

import uuid
from dataclasses import dataclass, field

from workspace.ai.services.llm import call_llm, extract_text_tool_calls


@dataclass(frozen=True)
class ToolCall:
    """One call the model asked for.

    ``arguments`` is the JSON text as the model wrote it, not a parsed
    dict: it is decoded once, by the registry, and the raw text is what
    the history echoes back to the model and what the record stores.
    """

    id: str
    name: str
    arguments: str

    def as_message_part(self) -> dict:
        """The call in the shape an assistant message carries it."""
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass
class ModelResponse:
    """One reply from the model, decoded.

    ``content`` is the text a reader should see, reasoning blocks and
    leaked artifacts stripped. ``raw_content`` is the text exactly as the
    backend produced it: it is what the assistant turn echoes back to the
    model on the next round, and what ``Message.tool_data`` keeps.
    """

    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    raw_content: str = ""

    @classmethod
    def from_call_llm(cls, result: dict) -> ModelResponse:
        """Read a :func:`call_llm` result dict.

        ``message`` is the SDK object when the reply came from the API;
        a caller that only has text may leave it out.
        """
        message = result.get("message")
        if message is None:
            raw_content = result.get("content") or ""
        else:
            raw_content = getattr(message, "content", None) or ""
        return cls(
            content=result.get("content") or "",
            thinking=result.get("thinking") or "",
            tool_calls=[
                ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments)
                for c in result.get("tool_calls") or []
            ],
            model=result.get("model") or "",
            prompt_tokens=result.get("prompt_tokens"),
            completion_tokens=result.get("completion_tokens"),
            raw_content=raw_content,
        )

    def as_assistant_message(self) -> dict:
        """The reply as the assistant turn appended to the conversation."""
        message = {"role": "assistant", "content": self.raw_content}
        if self.tool_calls:
            message["tool_calls"] = [tc.as_message_part() for tc in self.tool_calls]
        return message

    def as_record(self) -> dict:
        """The reply as ``AITask.raw_messages`` stores it."""
        return {
            "content": self.content,
            "thinking": self.thinking,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in self.tool_calls
            ]
            if self.tool_calls
            else None,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


def _with_text_tool_calls(response: ModelResponse) -> ModelResponse:
    """Read tool calls a model wrote out as JSON instead of calling natively.

    The call ids are minted here, since the backend gave none; the text the
    calls were cut from is what remains of the reply, on both faces.
    """
    raw_calls, remaining = extract_text_tool_calls(response.content)
    if not raw_calls:
        return response
    response.tool_calls = [
        ToolCall(id=f"call_{uuid.uuid4().hex[:24]}", name=name, arguments=args_json)
        for name, args_json in raw_calls
    ]
    response.content = remaining
    response.raw_content = remaining
    return response


class LLMModel:
    """The configured backend, reached through :func:`call_llm`.

    A reply to a request that offered tools is also read for calls written
    out as text, which is how a backend without native function calling
    still gets to use them. A tool-less request is never read that way: it
    is the run asking for a final answer, and JSON in it is the answer.
    """

    def __init__(self, name: str | None):
        self.name = name

    def complete(self, messages: list[dict], *, tools: list | None = None):
        result = call_llm(messages, model=self.name, tools=tools)
        response = ModelResponse.from_call_llm(result)
        if tools is not None and not response.tool_calls and response.content:
            response = _with_text_tool_calls(response)
        return response
