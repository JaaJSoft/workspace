"""The tools a bot can call: how a module declares one, what the agent gets.

A tool is a Pydantic AI tool. A module writes it as a method of a
:class:`ToolProvider`, marked with :func:`tool`, taking the run context and
at most one Pydantic model of arguments::

    @tool(badge_icon="🧠", badge_label="Retained", detail_key="key")
    def save_memory(self, ctx: RunContext[ToolDeps], args: SaveMemoryParams) -> str:
        '''Persistently save a fact about the user ...'''

The method name is the tool's name and its docstring the description. The
library reads the arguments' schema off the model, validates a call
against it, and tells the model what was wrong with one that does not fit.
"""

import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connections
from django.utils import translation
from pydantic_ai import Tool
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import FunctionToolset

from workspace.users.services.settings import get_user_language

# Appended to the reason of a failure the same call may not meet twice.
RETRYABLE_NOTE = "This failure is temporary: the same call may succeed if made again."


@dataclass
class ToolDeps:
    """Who a run acts for, and what its tools leave behind for the reply.

    *context* is a dict scoped to one reply: a tool puts there what the
    caller attaches once the run is over (images, a voice message, a
    question to the user), and reads there what kind of run it is in.
    """

    user: Any
    bot: Any
    conversation_id: Any = None
    context: dict = field(default_factory=dict)
    # Read once, where the run is put together: a tool on a pool thread
    # would otherwise open a connection there to look it up.
    language: str | None = None

    def __post_init__(self):
        if self.language is None and self.user is not None:
            self.language = get_user_language(self.user)


class ToolError(ToolFailed):
    """A tool call that did not produce what it was asked for.

    Raised from a tool, or anything it calls. The model reads *reason* in
    place of a result, and nothing was produced, saved or sent.

    *retryable* says whether the same call may succeed if made again: the
    service it depends on was down, timed out or rate-limited. A final
    failure (the default) will fail again with the same arguments - they
    were wrong, the target does not exist, the user may not touch it -
    so *reason* is where to say what to change, if anything can be.
    """

    def __init__(self, reason: str, *, retryable: bool = False):
        super().__init__(f"{reason} {RETRYABLE_NOTE}" if retryable else reason)
        self.reason = reason
        self.retryable = retryable


def tool(
    *,
    badge_icon: str = "⚡",
    badge_label: str | None = None,
    badge_running_label: str | None = None,
    detail_key: str | None = None,
    concurrent: bool = False,
):
    """Mark a :class:`ToolProvider` method as a tool.

    *badge_label* is past tense ("Generated image"): it labels a call that
    has already run. *badge_running_label* is the present participle
    ("Generating image") shown while the call is still in flight.
    *detail_key* names the argument shown beside the label.

    *concurrent* lets a round run this tool alongside its neighbours
    instead of alone. Set it only where the calls are independent: the tool
    must be safe off the thread that started the run, and must not read
    what a call beside it produces. Reading tools are the usual case; a
    paid one qualifies only when the wait it saves is worth losing the
    exact ordering of its budget checks, which today is generate_image
    alone. Everything else runs alone, in the order the model asked.
    """

    def decorator(fn):
        fn._tool_meta = {
            "badge_icon": badge_icon,
            "badge_label": badge_label,
            "badge_running_label": badge_running_label,
            "detail_key": detail_key,
            "concurrent": concurrent,
        }
        return fn

    return decorator


class ToolProvider:
    """Base class of the objects whose :func:`tool` methods are registered."""


_pool = None
_pool_lock = threading.Lock()


def _thread_pool():
    """The threads concurrent tools run on, as many as a round may use."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=settings.AI_TOOL_CONCURRENCY,
                thread_name_prefix="ai-tool",
            )
        return _pool


def _on_django_thread(handler, *, concurrent):
    """*handler* as the coroutine the agent awaits.

    A tool is synchronous Django code, and the agent runs on an event loop
    where the ORM refuses to work. A tool that runs alone goes back to the
    thread that started the run, where it finds that thread's database
    connection. A concurrent one takes a pool thread, and hands back the
    connections it opened there: nothing else closes them.

    Either way it runs in the user's language: a refusal another module
    translates reaches the chat in the words of whoever asked.
    """

    def run(ctx, **arguments):
        language = ctx.deps.language
        with translation.override(language) if language else nullcontext():
            return handler(ctx, **arguments)

    def run_in_pool(ctx, **arguments):
        try:
            return run(ctx, **arguments)
        finally:
            connections.close_all()

    @wraps(handler)
    async def call(ctx, **arguments):
        if concurrent and settings.AI_TOOL_CONCURRENCY > 1:
            return await sync_to_async(
                run_in_pool, thread_sensitive=False, executor=_thread_pool()
            )(ctx, **arguments)
        return await sync_to_async(run, thread_sensitive=True)(ctx, **arguments)

    return call


class ToolRegistry:
    """Every tool the modules registered, as one Pydantic AI toolset."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._lock = threading.Lock()

    def register_provider(self, provider: ToolProvider):
        """Register all ``@tool``-decorated methods from *provider*."""
        for name, method in inspect.getmembers(provider, predicate=callable):
            meta = getattr(method, "_tool_meta", None)
            if meta is None:
                continue
            description = (method.__doc__ or "").strip()
            if not description:
                raise ValueError(
                    f"Tool method '{name}' on {type(provider).__name__} "
                    f"must have a docstring (used as the tool description)"
                )
            label = meta["badge_label"] or name.replace("_", " ").title()
            registered = Tool(
                _on_django_thread(method, concurrent=meta["concurrent"]),
                takes_ctx=True,
                name=name,
                description=description,
                sequential=not meta["concurrent"],
                metadata={
                    "badge_icon": meta["badge_icon"],
                    "badge_label": label,
                    "badge_running_label": meta["badge_running_label"] or label,
                    "detail_key": meta["detail_key"],
                },
            )
            with self._lock:
                if name in self._tools:
                    raise ValueError(f"AI tool '{name}' is already registered")
                self._tools[name] = registered

    def get_all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def toolset(self) -> FunctionToolset[ToolDeps]:
        """The registered tools, as the toolset an agent is given."""
        return FunctionToolset(self.get_all())

    # -- display ------------------------------------------------------------

    def get_badge(self, name: str) -> dict:
        """Return ``{'icon', 'label', 'running_label'}`` for a tool name.

        ``label`` is past tense (the call is over), ``running_label`` the
        present participle shown while it is still in flight.
        """
        registered = self._tools.get(name)
        if not registered:
            return {"icon": "⚡", "label": name, "running_label": name}
        meta = registered.metadata
        return {
            "icon": meta["badge_icon"],
            "label": meta["badge_label"],
            "running_label": meta["badge_running_label"],
        }

    def get_detail(self, name: str, args: dict) -> str:
        """Extract the detail string shown next to the badge label.

        The badge is one truncated line, so a list-valued argument is read out
        rather than shown as its repr.
        """
        registered = self._tools.get(name)
        if not registered or not registered.metadata["detail_key"]:
            return ""
        value = args.get(registered.metadata["detail_key"])
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    def describe_call(self, name: str, arguments, max_len: int = 120) -> str:
        """``name(identifying argument)``, e.g. ``read_webpage(https://a.test)``.

        Reuses the badge's *detail_key* - the one argument that tells a reader
        which call this was - so a trimmed result can name the call that
        produces it again. *arguments* is a dict, or the JSON text of one.
        Kept to a single short line: it is quoted inside the residue of a
        truncated result, not read on its own.
        """
        if not isinstance(arguments, dict):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError, TypeError:
                arguments = {}
        detail = self.get_detail(name, arguments) if isinstance(arguments, dict) else ""
        detail = " ".join(detail.split())
        if not detail:
            return name
        if len(detail) > max_len:
            detail = detail[:max_len] + "…"
        return f"{name}({detail})"


tool_registry = ToolRegistry()
