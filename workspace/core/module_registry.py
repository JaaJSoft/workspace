import logging
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field

from workspace.common.metrics import safe_counter, safe_histogram
from workspace.common.query_deadline import QueryDeadlineExceeded, query_deadline

logger = logging.getLogger(__name__)

SEARCH_PROVIDER_DURATION = safe_histogram(
    "search_provider_duration_seconds",
    "Time one search provider took to answer a unified search, failures included",
    ["provider"],
)

SEARCH_PROVIDER_FAILURES = safe_counter(
    "search_provider_failures_total",
    "Search providers left out of a unified search answer, by reason (error/timeout)",
    ["provider", "reason"],
)

# Tailwind hue names a module may claim as its identity color. The same list
# drives the `.module-<hue>` rules in scripts/frontend/module-hues.js
# (core.tests.test_module_colors keeps the two in step). `red` is reserved
# for destructive semantics and `pink` for the AI feature, so neither is here.
MODULE_HUES = (
    "indigo",
    "sky",
    "emerald",
    "teal",
    "amber",
    "orange",
    "purple",
    "rose",
    "cyan",
    "slate",
    "lime",
    "fuchsia",
    "yellow",
)


@dataclass(frozen=True)
class ModuleInfo:
    name: str
    slug: str
    description: str
    icon: str
    color: str  # one of MODULE_HUES
    url: str | None
    active: bool = True
    order: int = 0
    preview: bool = False
    # False for modules that are real (page, search, commands, notifications)
    # but are tools used once rather than apps lived in: they leave the home
    # dashboard and the onboarding grid and are reached from the user menu.
    show_on_dashboard: bool = True


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


class SearchScope:
    """How much of what the user can open a scoped search provider returns.

    ``MINE`` keeps what lives in the user's own space and drops whatever they
    reach through a group, a share, a subscription or someone else's project.
    """

    ALL = "all"
    MINE = "mine"

    CHOICES = (ALL, MINE)


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
    date: str | None = None
    tags: tuple[SearchTag, ...] = ()


@dataclass(frozen=True)
class SearchProviderInfo:
    slug: str
    module_slug: str
    search_fn: Callable
    # Provider slugs this one supersedes when both answer with the same entity:
    # `notes` refines `files`, so a markdown note keeps its editor url.
    refines: tuple[str, ...] = ()
    # Whether search_fn takes a ``scope`` keyword (a SearchScope value). An
    # unscoped provider has nothing reached through someone else to drop, so
    # it answers the same under either scope.
    scoped: bool = False


@dataclass(frozen=True)
class SearchAnswer:
    hits: list[dict] = field(default_factory=list)
    # Providers that failed or ran out of time: their hits are not in there.
    missing: tuple[SearchProviderInfo, ...] = ()


@dataclass(frozen=True)
class CommandInfo:
    name: str
    keywords: list[str]
    icon: str
    url: str
    kind: str  # "navigate" | "action"
    module_slug: str
    order: int = 0


class ModuleRegistry:
    def __init__(self):
        self._modules: dict[str, ModuleInfo] = {}
        self._search_providers: dict[str, SearchProviderInfo] = {}
        self._commands: list[CommandInfo] = []
        self._lock = threading.Lock()

    def register(self, module: ModuleInfo):
        if module.color not in MODULE_HUES:
            raise ValueError(
                f"Module '{module.slug}' color '{module.color}' is not in MODULE_HUES"
            )
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

    def refinements(self) -> dict[str, frozenset[str]]:
        """Provider slug -> the provider slugs whose hits it supersedes."""
        return {
            provider.slug: frozenset(provider.refines)
            for provider in self._search_providers.values()
            if provider.refines
        }

    def search(
        self,
        query: str,
        user,
        limit: int = 10,
        scope: str = SearchScope.ALL,
        budget: float | None = None,
    ) -> SearchAnswer:
        """Every active provider's hits for *query*.

        With a *budget* (seconds), a provider whose queries run past it is
        interrupted and left out, rather than delaying the whole answer.
        """
        hits = []
        missing = []
        for provider in self._search_providers.values():
            module = self._modules.get(provider.module_slug)
            if not module or not module.active:
                continue
            started = time.monotonic()
            try:
                with query_deadline(budget) if budget else nullcontext():
                    found = self._provider_hits(
                        provider, module, query, user, limit, scope
                    )
            except QueryDeadlineExceeded:
                logger.warning(
                    "Search provider '%s' ran past its %gs budget",
                    provider.slug,
                    budget,
                )
                SEARCH_PROVIDER_FAILURES.labels(
                    provider=provider.slug, reason="timeout"
                ).inc()
                missing.append(provider)
            except Exception:
                logger.exception("Search provider '%s' failed", provider.slug)
                SEARCH_PROVIDER_FAILURES.labels(
                    provider=provider.slug, reason="error"
                ).inc()
                missing.append(provider)
            else:
                hits.extend(found)
            finally:
                SEARCH_PROVIDER_DURATION.labels(provider=provider.slug).observe(
                    time.monotonic() - started
                )
        return SearchAnswer(hits=hits, missing=tuple(missing))

    @staticmethod
    def _provider_hits(provider, module, query, user, limit, scope) -> list[dict]:
        if provider.scoped:
            found = provider.search_fn(query, user, limit, scope=scope)
        else:
            found = provider.search_fn(query, user, limit)
        # provider_slug names the kind of entity ("mail-contacts",
        # "project-tasks"); module_slug alone cannot tell two providers of
        # the same module apart. Built here, inside the budget: a provider
        # may hand back a lazy iterable.
        return [
            asdict(hit) | {"provider_slug": provider.slug, "module_color": module.color}
            for hit in found
        ]

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

    def command_payload(self, cmd: CommandInfo) -> dict:
        """The command as the palette JSON expects it, colored like its module."""
        return asdict(cmd) | {"color": self._modules[cmd.module_slug].color}

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
