"""Position of the tool call being executed, within the reply it belongs to.

A round dispatches independent calls together, so the order a tool appends
to the shared response context is completion order, not the order the model
asked for. Tools that leave something for the caller to attach - images,
today - stamp their position with it, and the caller sorts on that instead
of trusting the append order.

The position is scoped to the running thread: a parallel call sets its own
before the handler runs, and reads nothing from the calls beside it.
"""

import contextvars

_position = contextvars.ContextVar("ai_tool_call_position", default=0)


def call_position() -> int:
    """Position of the call being executed, counted over the whole reply."""
    return _position.get()


def set_call_position(position: int) -> None:
    """Record the position of the call about to run on this thread."""
    _position.set(position)
