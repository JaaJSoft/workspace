import logging
import threading
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActivityProviderInfo:
    slug: str
    label: str
    icon: str
    color: str
    provider_cls: type  # subclass of ActivityProvider


class ActivityProvider(ABC):
    """Base class for activity providers.

    Each app (chat, files, calendar, etc.) implements a subclass
    that knows how to query its own models for user activity data.
    """

    @abstractmethod
    def get_daily_counts(
        self, user_id: int | None, date_from: date, date_to: date, *, viewer_id: int | None = None,
    ) -> dict[date, int]:
        """Return a mapping of date -> activity count for the activity grid.

        When user_id is None, return counts across all users (filtered by viewer_id access).
        """

    @abstractmethod
    def get_recent_events(
        self, user_id: int | None, limit: int = 10, offset: int = 0, *, viewer_id: int | None = None,
    ) -> list[dict]:
        """Return recent activity events for the activity feed.

        When user_id is None, return events from all users (filtered by viewer_id access).

        Each dict must contain:
            icon        - CSS icon class (e.g. "hard-drive")
            label       - short human label (e.g. "File uploaded")
            description - one-line description
            timestamp   - datetime (timezone-aware)
            url         - link to the related object
        """

    @abstractmethod
    def get_stats(self, user_id: int | None, *, viewer_id: int | None = None) -> dict:
        """Return stat-card data for this provider."""


class ActivityRegistry:
    """Singleton thread-safe registry for activity providers."""

    def __init__(self):
        self._providers: dict[str, ActivityProviderInfo] = {}
        self._lock = threading.Lock()

    def register(self, info: ActivityProviderInfo):
        with self._lock:
            if info.slug in self._providers:
                raise ValueError(
                    f"Activity provider with slug '{info.slug}' is already registered"
                )
            self._providers[info.slug] = info

    def get_all(self) -> dict[str, ActivityProviderInfo]:
        return dict(self._providers)

    def get_provider(self, slug: str) -> ActivityProvider | None:
        info = self._providers.get(slug)
        if info is None:
            return None
        return info.provider_cls()

    def get_daily_counts(
        self, user_id: int | None, date_from: date, date_to: date, *, viewer_id: int | None = None,
    ) -> dict[date, int]:
        merged: dict[date, int] = defaultdict(int)
        for info in self._providers.values():
            try:
                provider = info.provider_cls()
                for day, count in provider.get_daily_counts(
                    user_id, date_from, date_to, viewer_id=viewer_id,
                ).items():
                    merged[day] += count
            except Exception:
                logger.exception("Activity provider '%s' failed in get_daily_counts", info.slug)
        return dict(merged)

    def get_recent_events(
        self,
        user_id: int | None,
        limit: int = 10,
        offset: int = 0,
        *,
        viewer_id: int | None = None,
        source: str | None = None,
        exclude_actor_id: int | None = None,
    ) -> list[dict]:
        if source is not None:
            info = self._providers.get(source)
            if info is None:
                return []
            try:
                provider = info.provider_cls()
                events = provider.get_recent_events(
                    user_id, limit=limit, offset=offset, viewer_id=viewer_id,
                )
                for event in events:
                    event.setdefault("source", info.slug)
                    event.setdefault("source_color", info.color)
                return events
            except Exception:
                logger.exception("Activity provider '%s' failed in get_recent_events", scrub(source))
                return []

        fetch_count = limit + offset
        all_events: list[dict] = []
        for info in self._providers.values():
            try:
                provider = info.provider_cls()
                events = provider.get_recent_events(
                    user_id, limit=fetch_count, offset=0, viewer_id=viewer_id,
                )
                for event in events:
                    event.setdefault("source", info.slug)
                    event.setdefault("source_color", info.color)
                if exclude_actor_id is not None:
                    events = [
                        e for e in events
                        if (e.get("actor") or {}).get("id") != exclude_actor_id
                    ]
                all_events.extend(events)
            except Exception:
                logger.exception("Activity provider '%s' failed in get_recent_events", info.slug)

        all_events.sort(key=lambda e: e["timestamp"], reverse=True)
        return all_events[offset:offset + limit]

    def get_stats(self, user_id: int | None, *, viewer_id: int | None = None) -> dict[str, dict]:
        stats: dict[str, dict] = {}
        for info in self._providers.values():
            try:
                provider = info.provider_cls()
                stats[info.slug] = provider.get_stats(user_id, viewer_id=viewer_id)
            except Exception:
                logger.exception("Activity provider '%s' failed in get_stats", info.slug)
                stats[info.slug] = {}
        return stats


activity_registry = ActivityRegistry()
