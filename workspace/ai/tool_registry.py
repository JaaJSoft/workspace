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
    badge_icon: str = '⚡',
    badge_label: str | None = None,
    detail_key: str | None = None,
    params: type[BaseModel] | None = None,
):
    """Mark a :class:`ToolProvider` method as an AI chat tool.

    The tool **name** is the method name and the **description** is its
    docstring.  Parameters are defined via *params* using a Pydantic
    ``BaseModel`` subclass.
    """
    def decorator(fn):
        fn._tool_meta = {
            'badge_icon': badge_icon,
            'badge_label': badge_label,
            'detail_key': detail_key,
            'params': params,
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
    detail_key: str | None = None
    params_class: type[BaseModel] | None = None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _build_parameters(params_cls: type[BaseModel] | None) -> dict:
    """Convert a Pydantic model class into an OpenAI parameters schema."""
    if params_cls is None:
        return {'type': 'object', 'properties': {}}
    schema = params_cls.model_json_schema()
    # Remove Pydantic metadata keys not needed by OpenAI
    schema.pop('title', None)
    schema.pop('$defs', None)
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
            meta = getattr(method, '_tool_meta', None)
            if meta is None:
                continue
            docstring = (method.__doc__ or '').strip()
            if not docstring:
                raise ValueError(
                    f"Tool method '{name}' on {type(provider).__name__} "
                    f"must have a docstring (used as the tool description)"
                )
            params_cls = meta['params']
            info = _ToolInfo(
                name=name,
                description=docstring,
                parameters=_build_parameters(params_cls),
                handler=method,
                badge_icon=meta['badge_icon'],
                badge_label=meta['badge_label'] or name.replace('_', ' ').title(),
                detail_key=meta['detail_key'],
                params_class=params_cls,
            )
            with self._lock:
                if info.name in self._tools:
                    raise ValueError(f"AI tool '{info.name}' is already registered")
                self._tools[info.name] = info

    # -- query --------------------------------------------------------------

    def get_all(self) -> list[_ToolInfo]:
        return list(self._tools.values())

    def get_definitions(self) -> list[dict]:
        """Return OpenAI function-calling definitions for all tools."""
        return [
            {
                'type': 'function',
                'function': {
                    'name': t.name,
                    'description': t.description,
                    'parameters': t.parameters,
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
            return 'Error: invalid JSON arguments'
        info = self._tools.get(name)
        if not info:
            return f'Unknown tool: {name}'
        if info.params_class is not None:
            try:
                args = info.params_class.model_validate(raw_args)
            except ValidationError as e:
                return f'Error: invalid arguments — {e}'
        else:
            args = raw_args
        if context is None:
            context = {}
        return info.handler(args, user, bot, conversation_id, context)

    # -- display ------------------------------------------------------------

    def get_badge(self, name: str) -> dict:
        """Return ``{'icon': ..., 'label': ...}`` for a tool name."""
        info = self._tools.get(name)
        if not info:
            return {'icon': '⚡', 'label': name}
        return {'icon': info.badge_icon, 'label': info.badge_label}

    def get_detail(self, name: str, args: dict) -> str:
        """Extract the detail string shown next to the badge label."""
        info = self._tools.get(name)
        if not info or not info.detail_key:
            return ''
        return args.get(info.detail_key, '')


tool_registry = ToolRegistry()
