from urllib.parse import urlencode

from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import dateformat, timezone

from workspace.common.search import apply_fulltext
from workspace.core.module_registry import SearchResult, SearchTag
from workspace.files.services.search_index import FILES_FTS, match_type_for
from workspace.photos.models import MediaItem
from workspace.photos.queries import (
    ALL,
    library_files,
    user_albums,
    user_face_clusters,
)
from workspace.photos.services.face_people import person_cards
from workspace.photos.services.timeline import UNDATED
from workspace.users.services.settings import get_user_timezone


def search_photos(query, user, limit):
    """People named in the photos, albums whose title matches, then photos
    and videos whose name does.

    People and albums come first: a name or a title is something the user
    wrote to find the photos again, and there are few of them.
    """
    people = search_people(query, user, limit)
    albums = search_albums(query, user, limit - len(people))
    return (
        people + albums + _search_media(query, user, limit - len(people) - len(albums))
    )


def search_people(query, user, limit):
    """The people named in the user's photos whose name holds *query*, each
    opening their photos. The contacts themselves are People's to find."""
    needle = (query or "").strip().lower()
    if limit <= 0 or not needle:
        return []
    clusters = (
        user_face_clusters(user)
        .filter(person__search_text__contains=needle, photo_count__gt=0)
        .select_related("person")
    )
    return [
        SearchResult(
            uuid=str(card.person.pk),
            name=card.person.display_name,
            url=f"{reverse('photos_ui:index')}?{urlencode({'person': card.person.pk})}",
            matched_value=card.person.display_name,
            match_type="name",
            type_icon="scan-face",
            module_slug="photos",
            tags=(SearchTag("Person", "info"),),
        )
        for card in person_cards(clusters)[:limit]
    ]


def search_albums(query, user, limit):
    """The albums the user can open whose title holds *query*, each opening
    the album."""
    if limit <= 0 or not (query or "").strip():
        return []
    albums = user_albums(user).filter(title__icontains=query.strip())
    return [
        SearchResult(
            uuid=str(album.uuid),
            name=album.title,
            url=reverse("photos_ui:album", args=[album.uuid]),
            matched_value=album.title,
            match_type="name",
            type_icon="book-image",
            module_slug="photos",
            tags=(SearchTag("Album", "success"),),
        )
        for album in albums.order_by(Lower("title"), "uuid")[:limit]
    ]


def _search_media(query, user, limit):
    """Photos and videos the user can open whose name matches, each opening
    the timeline on its day.

    The hit lands on the day the photo or video was taken (``?date=``) with
    it open in the viewer (``?open=``), so the result shows it among the ones
    taken around it rather than in whichever folder it was filed. A file
    outside the user's own files opens in the All library, the one that holds
    it whatever the reason the user can see it.
    """
    if limit <= 0:
        return []
    tz = get_user_timezone(user)
    group_ids = set(user.groups.values_list("pk", flat=True))
    qs = apply_fulltext(
        library_files(user, ALL).select_related("media_item", "parent"),
        query,
        index=FILES_FTS,
    ).order_by("-search_rank", "-updated_at")[:limit]

    results = []
    for f in qs:
        taken_at = f.media_item.taken_at
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
                type_icon=(
                    "video"
                    if f.media_item.media_type == MediaItem.MediaType.VIDEO
                    else "image"
                ),
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
