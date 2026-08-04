"""Presentation helpers for LLM tool calls.

Shared by the finished message's tool timeline and the live progress steps
pushed over SSE while a bot response generates, so a tool reads the same way
in both places.
"""

import json


def display_args(parsed):
    """Stringify parsed tool arguments as (key, value) pairs for display."""
    if not isinstance(parsed, dict):
        return []
    pairs = []
    for key, value in parsed.items():
        if isinstance(value, str):
            pairs.append((key, value))
        else:
            pairs.append((key, json.dumps(value, ensure_ascii=False)))
    return pairs
