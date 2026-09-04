"""Rules deciding whether a tool call runs, consulted before each dispatch.

A policy answers one question about one call: is it refused, and if so
what the model is told instead of a result. The first policy to refuse a
call settles it; the ones after it are not consulted, so a policy that
counts calls only counts the ones that reached it.
"""

import json
from collections import Counter

from .model import ToolCall


def _signature(call: ToolCall) -> str:
    """Identity of a call: same tool, same arguments.

    Arguments are re-serialized with sorted keys so a model that reorders
    them between two attempts is still recognized as repeating itself.
    """
    raw = call.arguments or ""
    try:
        args = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"))
    except json.JSONDecodeError, TypeError:
        args = raw.strip()
    return f"{call.name}:{args}"


class RepeatGuard:
    """Refuses a call already made *limit* times with the same arguments.

    A model stuck re-issuing one call burns every remaining round on an
    answer it already has. Refusing the duplicate keeps the side effects
    single and tells it why nothing new came back.
    """

    def __init__(self, limit: int):
        self._limit = limit
        self._seen = Counter()

    def refusal(self, call: ToolCall) -> str | None:
        self._seen[_signature(call)] += 1
        if self._seen[_signature(call)] <= self._limit:
            return None
        return (
            f"Not executed: this is the same call to {call.name} with the same "
            f"arguments, {self._limit} times already in this reply. Repeating it "
            "returns what you already have. Read the earlier result again, try a "
            "different tool or different arguments, or answer with what you know."
        )
