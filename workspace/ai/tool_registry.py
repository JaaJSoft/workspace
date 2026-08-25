import inspect
import json
import logging
import threading
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# @tool decorator
# ---------------------------------------------------------------------------


def tool(
    *,
    badge_icon: str = "⚡",
    badge_label: str | None = None,
    badge_running_label: str | None = None,
    detail_key: str | None = None,
    params: type[BaseModel] | None = None,
    concurrent: bool = False,
):
    """Mark a :class:`ToolProvider` method as an AI chat tool.

    The tool **name** is the method name and the **description** is its
    docstring.  Parameters are defined via *params* using a Pydantic
    ``BaseModel`` subclass.

    *badge_label* is past tense ("Generated image"): it labels a call that
    has already run. *badge_running_label* is the present participle
    ("Generating image") shown while the call is still in flight.

    *concurrent* lets the tool loop run this tool alongside its neighbours
    in the same round instead of waiting its turn. Set it only where the
    calls are independent: the handler must be safe off the main thread, must
    not read what a call beside it produces, and must let the caller put
    whatever it leaves behind back in call order. Reading tools are the
    usual case; a paid one qualifies only when the wait it saves is worth
    losing the exact ordering of its budget checks, which today is
    generate_image alone. Everything else stays sequential, the default.
    """

    def decorator(fn):
        fn._tool_meta = {
            "badge_icon": badge_icon,
            "badge_label": badge_label,
            "badge_running_label": badge_running_label,
            "detail_key": detail_key,
            "params": params,
            "concurrent": concurrent,
        }
        return fn

    return decorator


# ---------------------------------------------------------------------------
# ToolProvider base class
# ---------------------------------------------------------------------------


class ToolProvider:
    """Base class for AI tool providers.

    Subclass this and decorate methods with :func:`tool`.  Each decorated
    method becomes a chat tool whose handler receives
    ``(self, args, user, bot, conversation_id, context)`` and returns a ``str``.

    When a ``params`` model is set, *args* is a validated Pydantic instance.
    When ``params`` is ``None``, *args* is a raw ``dict``.

    *context* is a mutable dict scoped to a single response generation.
    Tools can store side-effects there (e.g. generated images) for the
    caller to process after the tool loop completes.
    """


# ---------------------------------------------------------------------------
# Internal data — ToolInfo (not part of the public API)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ToolInfo:
    name: str
    description: str
    parameters: dict
    handler: object  # callable(args, user, bot, conversation_id, context) -> str
    badge_icon: str
    badge_label: str
    badge_running_label: str
    detail_key: str | None = None
    params_class: type[BaseModel] | None = None
    concurrent: bool = False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _build_parameters(params_cls: type[BaseModel] | None) -> dict:
    """Convert a Pydantic model class into an OpenAI parameters schema."""
    if params_cls is None:
        return {"type": "object", "properties": {}}
    schema = params_cls.model_json_schema()
    # Remove Pydantic metadata keys not needed by OpenAI
    schema.pop("title", None)
    schema.pop("$defs", None)
    return schema


class ToolRegistry:
    """Singleton thread-safe registry for AI chat tools."""

    def __init__(self):
        self._tools: dict[str, _ToolInfo] = {}
        self._lock = threading.Lock()

    # -- registration -------------------------------------------------------

    def register_provider(self, provider: ToolProvider):
        """Register all ``@tool``-decorated methods from *provider*."""
        for name, method in inspect.getmembers(provider, predicate=callable):
            meta = getattr(method, "_tool_meta", None)
            if meta is None:
                continue
            docstring = (method.__doc__ or "").strip()
            if not docstring:
                raise ValueError(
                    f"Tool method '{name}' on {type(provider).__name__} "
                    f"must have a docstring (used as the tool description)"
                )
            params_cls = meta["params"]
            label = meta["badge_label"] or name.replace("_", " ").title()
            info = _ToolInfo(
                name=name,
                description=docstring,
                parameters=_build_parameters(params_cls),
                handler=method,
                badge_icon=meta["badge_icon"],
                badge_label=label,
                badge_running_label=meta["badge_running_label"] or label,
                detail_key=meta["detail_key"],
                params_class=params_cls,
                concurrent=meta["concurrent"],
            )
            with self._lock:
                if info.name in self._tools:
                    raise ValueError(f"AI tool '{info.name}' is already registered")
                self._tools[info.name] = info

    # -- query --------------------------------------------------------------

    def get_all(self) -> list[_ToolInfo]:
        return list(self._tools.values())

    def concurrent_names(self) -> frozenset[str]:
        """Names of the tools a round may run in parallel with each other."""
        return frozenset(t.name for t in self._tools.values() if t.concurrent)

    def get_definitions(self) -> list[dict]:
        """Return OpenAI function-calling definitions for all tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    # -- execution ----------------------------------------------------------

    def execute(self, tool_call, user, bot, conversation_id=None, context=None) -> str:
        """Execute a tool call and return the result string."""
        name = tool_call.function.name
        try:
            raw_args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            return "Error: invalid JSON arguments"
        info = self._tools.get(name)
        if not info:
            return f"Unknown tool: {name}"
        if info.params_class is not None:
            try:
                args = info.params_class.model_validate(raw_args)
            except ValidationError as e:
                return f"Error: invalid arguments — {e}"
        else:
            args = raw_args
        if context is None:
            context = {}
        return info.handler(args, user, bot, conversation_id, context)

    # -- display ------------------------------------------------------------

    def get_badge(self, name: str) -> dict:
        """Return ``{'icon', 'label', 'running_label'}`` for a tool name.

        ``label`` is past tense (the call is over), ``running_label`` the
        present participle shown while it is still in flight.
        """
        info = self._tools.get(name)
        if not info:
            return {"icon": "⚡", "label": name, "running_label": name}
        return {
            "icon": info.badge_icon,
            "label": info.badge_label,
            "running_label": info.badge_running_label,
        }

    def get_detail(self, name: str, args: dict) -> str:
        """Extract the detail string shown next to the badge label.

        The badge is one truncated line, so a list-valued argument is read out
        rather than shown as its repr.
        """
        info = self._tools.get(name)
        if not info or not info.detail_key:
            return ""
        value = args.get(info.detail_key)
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    def describe_call(self, name: str, raw_arguments: str, max_len: int = 120) -> str:
        """``name(identifying argument)``, e.g. ``read_webpage(https://a.test)``.

        Reuses the badge's *detail_key* - the one argument that tells a reader
        which call this was - so a trimmed result can name the call that
        produces it again. Kept to a single short line: it is quoted inside
        the residue of a truncated result, not read on its own.
        """
        try:
            args = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError, TypeError:
            args = {}
        detail = self.get_detail(name, args) if isinstance(args, dict) else ""
        detail = " ".join(detail.split())
        if not detail:
            return name
        if len(detail) > max_len:
            detail = detail[:max_len] + "…"
        return f"{name}({detail})"


tool_registry = ToolRegistry()
