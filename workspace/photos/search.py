from django.utils import dateformat, timezone

from workspace.common.search import apply_fulltext
from workspace.core.module_registry import SearchResult, SearchTag
from workspace.files.services.search_index import FILES_FTS, match_type_for
from workspace.photos.queries import library_files
from workspace.photos.services.timeline import UNDATED
from workspace.users.services.settings import get_user_timezone


def search_photos(query, user, limit):
    """Photos whose name matches, each opening the timeline on its day.

    The hit lands on the day the photo was taken (``?date=``) with the photo
    open in the viewer (``?open=``), so the result shows the picture among the
    ones taken around it rather than in whichever folder it was filed.
    """
    tz = get_user_timezone(user)
    qs = apply_fulltext(
        library_files(user).select_related("photo", "parent"),
        query,
        index=FILES_FTS,
    ).order_by("-search_rank", "-updated_at")[:limit]

    results = []
    for f in qs:
        taken_at = f.photo.taken_at
        day = timezone.localtime(taken_at, tz).date() if taken_at else None
        date_param = day.isoformat() if day else UNDATED
        results.append(
            SearchResult(
                uuid=str(f.uuid),
                name=f.name,
                url=f"/photos?date={date_param}&open={f.uuid}",
                matched_value=f.name,
                match_type=match_type_for(f.name, query),
                type_icon="image",
                module_slug="photos",
                date=dateformat.format(day, "j M Y") if day else None,
                tags=(SearchTag(f.parent.name, "success"),) if f.parent else (),
            )
        )
    return results
