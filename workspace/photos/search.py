from urllib.parse import urlencode

from django.utils import dateformat, timezone

from workspace.common.search import apply_fulltext
from workspace.core.module_registry import SearchResult, SearchTag
from workspace.files.services.search_index import FILES_FTS, match_type_for
from workspace.photos.queries import ALL, library_files
from workspace.photos.services.timeline import UNDATED
from workspace.users.services.settings import get_user_timezone


def search_photos(query, user, limit):
    """Photos the user can open whose name matches, each opening the timeline
    on its day.

    The hit lands on the day the photo was taken (``?date=``) with the photo
    open in the viewer (``?open=``), so the result shows the picture among the
    ones taken around it rather than in whichever folder it was filed. A photo
    outside the user's own files opens in the All library, the one that holds
    it whatever the reason the user can see it.
    """
    tz = get_user_timezone(user)
    group_ids = set(user.groups.values_list("pk", flat=True))
    qs = apply_fulltext(
        library_files(user, ALL).select_related("photo", "parent"),
        query,
        index=FILES_FTS,
    ).order_by("-search_rank", "-updated_at")[:limit]

    results = []
    for f in qs:
        taken_at = f.photo.taken_at
        day = timezone.localtime(taken_at, tz).date() if taken_at else None
        params = {"date": day.isoformat() if day else UNDATED, "open": f.uuid}
        personal = f.owner_id == user.pk and f.group_id is None
        if not personal:
            params = {"scope": ALL} | params
        # A file shared on its own says nothing about the folder around it,
        # which belongs to someone else.
        reachable_parent = personal or f.group_id in group_ids
        results.append(
            SearchResult(
                uuid=str(f.uuid),
                name=f.name,
                url=f"/photos?{urlencode(params)}",
                matched_value=f.name,
                match_type=match_type_for(f.name, query),
                type_icon="image",
                module_slug="photos",
                date=dateformat.format(day, "j M Y") if day else None,
                tags=(
                    (SearchTag(f.parent.name, "success"),)
                    if f.parent and reachable_parent
                    else ()
                ),
            )
        )
    return results
