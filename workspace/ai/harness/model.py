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

    ``content`` is the text with the reasoning and the leaked artifacts
    stripped out, and ``thinking`` the reasoning on its own. The assistant
    turn echoes ``content`` back to the model on the next round and
    ``Message.tool_data`` keeps it: reasoning is never replayed, whether
    the backend put it in a field or between inline tags.
    """

    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @classmethod
    def from_call_llm(cls, result: dict) -> ModelResponse:
        """Read a :func:`call_llm` result dict."""
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
        )

    def as_assistant_message(self) -> dict:
        """The reply as the assistant turn appended to the conversation."""
        message = {"role": "assistant", "content": self.content}
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
    calls were cut from is what remains of the reply.
    """
    raw_calls, remaining = extract_text_tool_calls(response.content)
    if not raw_calls:
        return response
    response.tool_calls = [
        ToolCall(id=f"call_{uuid.uuid4().hex[:24]}", name=name, arguments=args_json)
        for name, args_json in raw_calls
    ]
    response.content = remaining
    return response


class LLMModel:
    """The configured backend, reached through :func:`call_llm`.

    A reply to a request that offered tools is also read for calls written
    out as text, which is how a backend without native function calling
    still gets to use them. A request that offered none is never read that
    way: it is the run asking for a final answer, or a run with no tool to
    call, and JSON in it is the answer.
    """

    def __init__(self, name: str | None):
        self.name = name

    def complete(self, messages: list[dict], *, tools: list | None = None):
        result = call_llm(messages, model=self.name, tools=tools)
        response = ModelResponse.from_call_llm(result)
        if tools and not response.tool_calls and response.content:
            response = _with_text_tool_calls(response)
        return response
