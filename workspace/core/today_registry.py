"""Sources of the dashboard's Today card.

A module registers a ``TodaySource`` from its ``AppConfig.ready()``: the
query stays in the module, next to its access rules, and so does the
template that renders one of its rows. The dashboard merges every source
into one list, urgent rows first, and lets the user filter it by source.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass

from .services.module_visibility import is_module_slug_visible


@dataclass(frozen=True)
class TodayRows:
    """What a source lists: its first rows, and how many it has in all."""

    items: list
    total: int


def capped_rows(queryset, limit):
    """The first *limit* rows of *queryset* and its size.

    Counts in the database only when the page is full: a shorter page is
    already the whole set.
    """
    items = list(queryset[:limit])
    total = queryset.count() if len(items) == limit else len(items)
    return TodayRows(items, total)


def never_urgent(item):
    return False


@dataclass(frozen=True)
class TodaySource:
    slug: str
    module_slug: str
    label: str
    # One line under the source's toggle in the dashboard preferences.
    description: str
    icon: str
    url: str
    # (user, now, tz) -> TodayRows, already capped by the source. No
    # items: the source stays out of the card.
    items_fn: Callable
    # Renders one row, the link inside its <li>, with ``item`` and
    # ``urgent`` in context.
    template: str
    # item -> bool: the row goes to the top of the list, whatever its source.
    is_urgent: Callable = never_urgent
    # Rows that are not urgent follow their source's order.
    order: int = 100


class TodayRegistry:
    def __init__(self):
        self._sources: dict[str, TodaySource] = {}
        self._lock = threading.Lock()

    def register(self, source: TodaySource):
        with self._lock:
            if source.slug in self._sources:
                raise ValueError(
                    f"Today source with slug '{source.slug}' is already registered"
                )
            self._sources[source.slug] = source

    def unregister(self, slug):
        with self._lock:
            self._sources.pop(slug, None)

    def visible_sources(self, user) -> list[TodaySource]:
        """The sources whose module *user* may see, in display order."""
        return sorted(
            (
                source
                for source in self._sources.values()
                if is_module_slug_visible(user, source.module_slug)
            ),
            key=lambda source: (source.order, source.slug),
        )


today_registry = TodayRegistry()
