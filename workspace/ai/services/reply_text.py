"""What is done to text on its way out of a model or into storage.

A reply carries artifacts: reasoning written between inline tags, the
history's own markers imitated back. A stored tool result is cut to a
budget. Neither depends on which model answered.
"""

import re

# Backends spell their inline reasoning tag differently. The backreference keeps
# an opening tag from being closed by a different one, and the body refuses to
# cross a further opening tag so a mismatched closer cannot merge two blocks -
# and with them the answer in between - into a single one.
_THINK_TAGS = "think|thinking|thought|thoughts|reasoning"
_THINK_RE = re.compile(
    rf"<({_THINK_TAGS})>((?:(?!<(?:{_THINK_TAGS})>)[\s\S])*?)</\1>\s*",
    re.IGNORECASE,
)
_RAW_TOOL_CALL_RE = re.compile(r"</?tool_call>", re.IGNORECASE)
# Matches the history header leaked by the LLM: the bracketed line
# conversation_history puts before each message ("[2026-04-10 20:07 | Your
# reply.] ..."), or a bare timestamp with or without brackets.
_TIMESTAMP_PREFIX_RE = re.compile(
    r"^(?:\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}[^\]\n]*\]"
    r"|\[?\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\]?)\s*"
)
# Image stand-ins injected by conversation_history; the model imitates them.
# Stripped anywhere (never legitimate output); the leading newline run is put back
# so a standalone marker leaves no blank line, and the `!` lookbehind spares a
# markdown image whose alt text starts with "image:".
_IMAGE_MARKER_RE = re.compile(
    r"(\n*)(?<!!)\[(?:Images sent by the assistant in the message above"
    r"|image:[^\n]*?)\][ \t]*\n*",
    re.IGNORECASE,
)


def clean_llm_content(content: str) -> str:
    """Strip artifacts that LLMs sometimes leak into their replies."""
    content = _RAW_TOOL_CALL_RE.sub("", content)
    # Markers first: _TIMESTAMP_PREFIX_RE is anchored, so a timestamp sitting
    # behind one is only reachable once the marker is gone.
    content = _IMAGE_MARKER_RE.sub(r"\1", content)
    content = _TIMESTAMP_PREFIX_RE.sub("", content)
    return content.strip()


def split_inline_thinking(content: str) -> tuple[str, str]:
    """Split inline reasoning blocks (<think>, <thought>, ...) out of model output.

    Returns (thinking, cleaned). Multiple blocks join with a blank line.
    An unclosed tag matches nothing: it stays in the content and captures no
    thinking.
    """
    blocks = [m.group(2).strip() for m in _THINK_RE.finditer(content)]
    thinking = "\n\n".join(b for b in blocks if b)
    cleaned = _THINK_RE.sub("", content).strip()
    return thinking, cleaned


def truncate_middle(text, max_len, hint=""):
    """Cap *text* at *max_len* characters by removing its middle.

    A fetched page, a JSON payload or a task list carries its conclusion at
    the end, so a head-only cut drops exactly what the text was read for.
    *hint* names the call that produced the text, which leaves the residue a
    pointer to something obtainable again instead of a dead end. The marker
    counts against the budget: a cap that quietly returns more is not a cap.
    """
    if len(text) <= max_len:
        return text
    source = f" of {hint}" if hint else ""

    def marker(omitted):
        return (
            f"\n\n[… {omitted} characters omitted from the middle{source}; "
            "call the tool again to read the full result …]\n\n"
        )

    # len(text) is an upper bound on the omitted count, hence on the marker's
    # width: sizing on it keeps the result within budget once the real (never
    # longer) number is written in.
    keep = max_len - len(marker(len(text)))
    if keep <= 0:
        return text[:max_len]
    head, tail = keep - keep // 2, keep // 2
    return text[:head] + marker(len(text) - keep) + text[len(text) - tail :]
