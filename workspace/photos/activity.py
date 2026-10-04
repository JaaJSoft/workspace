"""Album activity for the activity feed: albums shared, photos added.

An addition is one event per album, contributor and day, never one per
photo: adding a trip's three hundred photos is one thing done. Someone
looking at another user's activity only sees the albums they can open
themselves.
"""

from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDate
from django.urls import reverse

from workspace.common.datetimes import local_date_range
from workspace.core.activity_registry import ActivityProvider


def _actor(user):
    if user is None:
        return None
    return {"id": user.pk, "username": user.username, "full_name": user.get_full_name()}


def _count_label(count):
    return "1 photo" if count == 1 else f"{count} photos"


class PhotosActivityProvider(ActivityProvider):
    def _album_filter(self, user_id, viewer_id, field="album_id"):
        """Rows about albums the viewer can open; all of them when the
        viewer is looking at their own activity."""
        if viewer_id is None or viewer_id == user_id:
            return Q()
        from django.contrib.auth import get_user_model

        from workspace.photos.queries import user_albums

        viewer = get_user_model().objects.get(pk=viewer_id)
        return Q(**{f"{field}__in": user_albums(viewer).values("pk")})

    def _shares(self, user_id, viewer_id, exclude_actor_id=None):
        from workspace.photos.models import AlbumShare

        shares = AlbumShare.objects.filter(shared_by__isnull=False)
        if user_id is not None:
            shares = shares.filter(shared_by_id=user_id)
        if exclude_actor_id is not None:
            shares = shares.exclude(shared_by_id=exclude_actor_id)
        return shares.filter(self._album_filter(user_id, viewer_id))

    def _additions(self, user_id, viewer_id, exclude_actor_id=None):
        """The album items, grouped by album, contributor and day."""
        from workspace.photos.models import AlbumItem

        items = AlbumItem.objects.filter(added_by__isnull=False)
        if user_id is not None:
            items = items.filter(added_by_id=user_id)
        if exclude_actor_id is not None:
            items = items.exclude(added_by_id=exclude_actor_id)
        return (
            items.filter(self._album_filter(user_id, viewer_id))
            .annotate(day=TruncDate("added_at"))
            .order_by()
            .values("album_id", "added_by_id", "day")
            .annotate(count=Count("pk"), at=Max("added_at"))
        )

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        start, end = local_date_range(date_from, date_to)
        counts = {}
        shares = (
            self._shares(user_id, viewer_id)
            .filter(created_at__gte=start, created_at__lt=end)
            .annotate(day=TruncDate("created_at"))
            .order_by()
            .values("day")
            .annotate(count=Count("pk"))
        )
        for row in shares:
            counts[row["day"]] = counts.get(row["day"], 0) + row["count"]
        additions = self._additions(user_id, viewer_id).filter(
            added_at__gte=start, added_at__lt=end
        )
        for row in additions:
            counts[row["day"]] = counts.get(row["day"], 0) + 1
        return counts

    def get_recent_events(
        self, user_id, limit=10, offset=0, *, viewer_id=None, exclude_actor_id=None
    ):
        from django.contrib.auth import get_user_model

        from workspace.photos.models import Album

        wanted = offset + limit
        shares = list(
            self._shares(user_id, viewer_id, exclude_actor_id)
            .select_related("album", "shared_by")
            .order_by("-created_at")[:wanted]
        )
        additions = list(
            self._additions(user_id, viewer_id, exclude_actor_id).order_by("-at")[
                :wanted
            ]
        )
        albums = Album.objects.in_bulk({row["album_id"] for row in additions})
        adders = get_user_model().objects.in_bulk(
            {row["added_by_id"] for row in additions}
        )

        events = [
            {
                "icon": "share-2",
                "label": "Album shared",
                "description": share.album.title,
                "timestamp": share.created_at,
                "url": reverse("photos_ui:album", args=[share.album_id]),
                "actor": _actor(share.shared_by),
            }
            for share in shares
        ]
        events += [
            {
                "icon": "image-plus",
                "label": "Photos added",
                "description": (
                    f"{_count_label(row['count'])} to {albums[row['album_id']].title}"
                ),
                "timestamp": row["at"],
                "url": reverse("photos_ui:album", args=[row["album_id"]]),
                "actor": _actor(adders.get(row["added_by_id"])),
            }
            for row in additions
        ]
        events.sort(key=lambda event: event["timestamp"], reverse=True)
        return events[offset:wanted]

    def get_stats(self, user_id, *, viewer_id=None):
        from workspace.photos.models import Album

        albums = Album.objects.all()
        if user_id is not None:
            albums = albums.filter(owner_id=user_id)
        albums = albums.filter(self._album_filter(user_id, viewer_id, field="pk"))
        return {"total_albums": albums.count()}
