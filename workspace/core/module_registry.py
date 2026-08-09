import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModuleInfo:
    name: str
    slug: str
    description: str
    icon: str
    color: str
    url: str | None
    active: bool = True
    order: int = 0
    preview: bool = False


class ModuleVisibility:
    """Audience levels for module visibility (most open to most restrictive)."""

    ALL = "all"
    STAFF = "staff"
    ADMIN = "admin"
    NONE = "none"

    CHOICES = (ALL, STAFF, ADMIN, NONE)

    @classmethod
    def normalize(cls, value, default="staff"):
        """Lowercase and validate a level string; fall back to `default`."""
        value = (value or "").strip().lower()
        return value if value in cls.CHOICES else default


@dataclass(frozen=True)
class SearchTag:
    label: str
    color: str = "ghost"


@dataclass(frozen=True)
class SearchResult:
    uuid: str
    name: str
    url: str
    matched_value: str
    match_type: str
    type_icon: str
    module_slug: str
    module_color: str
    date: str | None = None
    tags: tuple[SearchTag, ...] = ()


@dataclass(frozen=True)
class SearchProviderInfo:
    slug: str
    module_slug: str
    search_fn: Callable


@dataclass(frozen=True)
class PendingAction:
    """Badge count for a module tile, with an optional deep link.

    ``url`` is set only when the pending work has a single unambiguous
    target (one unread conversation, one overdue task, ...) so the tile can
    open that item directly. It stays ``None`` as soon as the target is
    ambiguous, and the tile falls back to the module home.
    """

    count: int
    url: str | None = None


@dataclass(frozen=True)
class PendingActionProviderInfo:
    module_slug: str
    pending_action_fn: Callable  # signature: (user) -> PendingAction


@dataclass(frozen=True)
class CommandInfo:
    name: str
    keywords: list[str]
    icon: str
    color: str
    url: str
    kind: str  # "navigate" | "action"
    module_slug: str
    order: int = 0


class ModuleRegistry:
    def __init__(self):
        self._modules: dict[str, ModuleInfo] = {}
        self._search_providers: dict[str, SearchProviderInfo] = {}
        self._pending_action_providers: dict[str, PendingActionProviderInfo] = {}
        self._commands: list[CommandInfo] = []
        self._lock = threading.Lock()

    def register(self, module: ModuleInfo):
        with self._lock:
            if module.slug in self._modules:
                raise ValueError(
                    f"Module with slug '{module.slug}' is already registered"
                )
            self._modules[module.slug] = module

    def register_search_provider(self, provider: SearchProviderInfo):
        with self._lock:
            if provider.module_slug not in self._modules:
                raise ValueError(
                    f"Module '{provider.module_slug}' must be registered before its search provider"
                )
            if provider.slug in self._search_providers:
                raise ValueError(
                    f"Search provider '{provider.slug}' is already registered"
                )
            self._search_providers[provider.slug] = provider

    def search(self, query: str, user, limit: int = 10) -> list[dict]:
        results = []
        for provider in self._search_providers.values():
            module = self._modules.get(provider.module_slug)
            if not module or not module.active:
                continue
            try:
                hits = provider.search_fn(query, user, limit)
                # provider_slug names the kind of entity ("mail-contacts",
                # "project-tasks"); module_slug alone cannot tell two
                # providers of the same module apart.
                results.extend(
                    asdict(h) | {"provider_slug": provider.slug} for h in hits
                )
            except Exception:
                logger.exception("Search provider '%s' failed", provider.slug)
        return results

    def register_pending_action_provider(self, provider: PendingActionProviderInfo):
        with self._lock:
            if provider.module_slug not in self._modules:
                raise ValueError(
                    f"Module '{provider.module_slug}' must be registered before its pending action provider"
                )
            if provider.module_slug in self._pending_action_providers:
                raise ValueError(
                    f"Pending action provider for '{provider.module_slug}' is already registered"
                )
            self._pending_action_providers[provider.module_slug] = provider

    def get_pending_actions(self, user) -> dict[str, PendingAction]:
        actions = {}
        for slug, provider in self._pending_action_providers.items():
            module = self._modules.get(slug)
            if not module or not module.active:
                continue
            try:
                actions[slug] = provider.pending_action_fn(user)
            except Exception:
                logger.exception("Pending action provider '%s' failed", slug)
                actions[slug] = PendingAction(count=0)
        return actions

    def register_commands(self, commands: list[CommandInfo]):
        with self._lock:
            for cmd in commands:
                if cmd.module_slug not in self._modules:
                    raise ValueError(
                        f"Module '{cmd.module_slug}' must be registered before its commands"
                    )
            self._commands.extend(commands)

    def get_active_commands(self) -> list[CommandInfo]:
        return sorted(
            (
                cmd
                for cmd in self._commands
                if (m := self._modules.get(cmd.module_slug)) and m.active
            ),
            key=lambda c: c.order,
        )

    def search_commands(self, query: str) -> list[CommandInfo]:
        q = query.lower()
        name_matches = []
        keyword_matches = []
        for cmd in self._commands:
            module = self._modules.get(cmd.module_slug)
            if not module or not module.active:
                continue
            if q in cmd.name.lower():
                name_matches.append(cmd)
            elif any(q in kw.lower() for kw in cmd.keywords):
                keyword_matches.append(cmd)
        name_matches.sort(key=lambda c: c.order)
        keyword_matches.sort(key=lambda c: c.order)
        return name_matches + keyword_matches

    def get(self, slug: str) -> ModuleInfo | None:
        return self._modules.get(slug)

    def get_all(self) -> list[ModuleInfo]:
        return sorted(self._modules.values(), key=lambda m: m.order)

    def get_active(self) -> list[ModuleInfo]:
        return [m for m in self.get_all() if m.active]


registry = ModuleRegistry()
