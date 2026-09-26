from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import render
from django.urls import reverse
from django.utils import dateformat, timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.common.booleans import is_truthy
from workspace.common.uuids import parse_uuid_or_none
from workspace.files.models import Tag
from workspace.photos.models import Album, MediaItem
from workspace.photos.queries import (
    ALL,
    MINE,
    SHARED,
    album_date_range,
    album_files,
    face_progress,
    has_shared_photos,
    library_files,
    library_groups,
    library_tags,
    media_type_counts,
    reachable_album,
    unanalyzed_count,
    user_face_clusters,
)
from workspace.photos.services.album_cards import album_cards, user_album_cards
from workspace.photos.services.face_preferences import faces_available, faces_enabled
from workspace.photos.services.timeline import (
    START,
    UNDATED,
    manual_page,
    mark_favorite_toggles,
    parse_cursor,
    parse_date_position,
    parse_manual_cursor,
    timeline_page,
    with_album_positions,
    with_timeline_fields,
    year_counts,
)
from workspace.users.services.settings import (
    get_module_settings,
    get_setting,
    get_user_timezone,
)

# Tile width in px at each step of the size slider, the ``photos`` /
# ``tile_size`` setting (1 to 5, as the Files mosaic). Phones keep three
# tiles a row whatever the step.
TILE_WIDTHS = (96, 120, 144, 192, 256)
DEFAULT_TILE_SIZE = 3


def _tile_size(user):
    """The user's slider step; anything malformed falls back to the default."""
    size = get_setting(user, "photos", "tile_size", default=DEFAULT_TILE_SIZE)
    if isinstance(size, bool) or not isinstance(size, int):
        return DEFAULT_TILE_SIZE
    return size if 1 <= size <= len(TILE_WIDTHS) else DEFAULT_TILE_SIZE


def _scope(request):
    """The library ``?scope=`` asks for; 404 on a group the user is not in."""
    raw = request.GET.get("scope", "")
    if raw in ("", MINE):
        return MINE
    if raw in (SHARED, ALL):
        return raw
    prefix, _, group_id = raw.partition(":")
    if prefix == "group":
        try:
            group = request.user.groups.filter(pk=int(group_id)).first()
        except ValueError:
            group = None
        if group is not None:
            return group
    raise Http404


def _scope_params(scope):
    if scope == MINE:
        return {}
    if scope in (SHARED, ALL):
        return {"scope": scope}
    return {"scope": f"group:{scope.pk}"}


def _filters(request):
    """The active filters as ``(favorites, media_type, tag)``.

    404 on a tag not the user's, 400 on a ``?type=`` that is neither
    ``photo`` nor ``video``.
    """
    favorites = is_truthy(request.GET.get("favorites"))
    media_type = None
    if request.GET.get("type"):
        if request.GET["type"] not in MediaItem.MediaType.values:
            raise BadRequest("Invalid media type.")
        media_type = MediaItem.MediaType(request.GET["type"])
    tag = None
    if request.GET.get("tag"):
        tag_uuid = parse_uuid_or_none(request.GET["tag"])
        if tag_uuid is not None:
            tag = Tag.objects.filter(owner=request.user, uuid=tag_uuid).first()
        if tag is None:
            raise Http404
    return favorites, media_type, tag


def _cluster(request):
    """The face cluster ``?cluster=`` asks for, or None; 404 on one not the user's."""
    raw = request.GET.get("cluster")
    if not raw:
        return None
    cluster_uuid = parse_uuid_or_none(raw)
    cluster = (
        user_face_clusters(request.user).filter(pk=cluster_uuid).first()
        if cluster_uuid is not None
        else None
    )
    if cluster is None:
        raise Http404
    return cluster


def _filtered_library(user, scope, favorites, tag, cluster=None):
    """The library in *scope* narrowed by the sidebar view, every media type."""
    files = library_files(user, scope)
    if favorites:
        files = files.filter(favorites__owner=user)
    if tag is not None:
        files = files.filter(file_tags__tag=tag)
    if cluster is not None:
        files = files.filter(faces__cluster=cluster)
    return files


def _of_type(files, media_type):
    if media_type is None:
        return files
    return files.filter(media_item__media_type=media_type)


def _view_filter_params(favorites, tag, cluster=None):
    params = {}
    if favorites:
        params["favorites"] = "1"
    if tag is not None:
        params["tag"] = str(tag.uuid)
    if cluster is not None:
        params["cluster"] = str(cluster.pk)
    return params


def _type_params(media_type):
    return {"type": media_type.value} if media_type is not None else {}


def _type_tabs(counts, media_type, params):
    """The media type switcher: All, Photos, Videos.

    Hidden (empty) while the view holds a single kind, as there is nothing to
    narrow: it stays up once a type is picked, so the way back is always there.
    """
    if media_type is None and not (counts["photos"] and counts["videos"]):
        return []
    choices = [
        (None, "All", "Photos and videos", "image-play"),
        (MediaItem.MediaType.PHOTO, "Photos", "Photos only", "image"),
        (MediaItem.MediaType.VIDEO, "Videos", "Videos only", "video"),
    ]
    return [
        {
            "label": label,
            "title": title,
            "icon": icon,
            "url": _url_with(params | _type_params(choice)),
            "active": choice == media_type,
        }
        for choice, label, title, icon in choices
    ]


def _scope_tabs(user, scope, filter_params):
    """The library switcher: Mine, All, Shared with me, then one tab per group.

    Shared with me and the groups only get a tab when they hold a photo. The
    bar is hidden (empty) while there is nothing to switch to: All would show
    the very same pictures as Mine.
    """
    groups = list(library_groups(user))
    if not isinstance(scope, str) and scope not in groups:
        groups.append(scope)
    shared = scope == SHARED or has_shared_photos(user)
    if not groups and not shared and scope == MINE:
        return []
    choices = [(MINE, "Mine", "user"), (ALL, "All", "layers")]
    if shared:
        choices.append((SHARED, "Shared with me", "share-2"))
    choices += [(group, group.name, "users") for group in groups]
    return [
        {
            "label": label,
            "icon": icon,
            "url": _url_with(_scope_params(choice) | filter_params),
            "active": choice == scope,
        }
        for choice, label, icon in choices
    ]


def _page_context(request, page, page_url, view_params=None):
    mark_favorite_toggles(page.photos, request.user)
    next_url = None
    if page.next_cursor:
        params = (view_params or {}) | {"cursor": page.next_cursor}
        next_url = f"{page_url}?{urlencode(params)}"
    return {"entries": page.entries, "next_url": next_url}


def _timeline_page_context(request, files, position, tz, view_params):
    page = timeline_page(with_timeline_fields(files, request.user), position, tz)
    return _page_context(request, page, reverse("photos_ui:timeline"), view_params)


def _album_page_context(request, album, files, cursor, tz):
    """The first page of *album* when *cursor* is None, else the one after it.

    ValueError on a cursor that is not one of the album's sort mode.
    """
    files = with_timeline_fields(files, request.user)
    if album.sort_mode == Album.SortMode.MANUAL:
        after = parse_manual_cursor(cursor) if cursor is not None else None
        page = manual_page(with_album_positions(files, album), after)
    else:
        position = parse_cursor(cursor) if cursor is not None else START
        page = timeline_page(files, position, tz)
    page_url = reverse("photos_ui:album_timeline", args=[album.uuid])
    return _page_context(request, page, page_url) | {
        "manual_order": album.sort_mode == Album.SortMode.MANUAL
    }


def _sidebar_albums(user, active_album=None):
    return [
        {
            "card": card,
            "url": reverse("photos_ui:album", args=[card.album.uuid]),
            "active": active_album is not None and card.album.pk == active_album.pk,
        }
        for card in user_album_cards(user)
    ]


def _date_range_label(first, last, tz):
    """The capture dates an album spans, as "14 Jul - 2 Aug 2024"."""
    if first is None:
        return ""
    first = timezone.localtime(first, tz).date()
    last = timezone.localtime(last, tz).date()
    if first == last:
        return dateformat.format(first, "j M Y")
    if first.year == last.year:
        return f"{dateformat.format(first, 'j M')} - {dateformat.format(last, 'j M Y')}"
    return f"{dateformat.format(first, 'j M Y')} - {dateformat.format(last, 'j M Y')}"


def _count_label(counts, media_type):
    """What the listing holds, as "12 photos, 3 videos".

    A kind it holds none of, or that *media_type* filters out, is left out,
    unless both are.
    """
    if media_type == MediaItem.MediaType.PHOTO:
        counts = counts | {"videos": 0}
    elif media_type == MediaItem.MediaType.VIDEO:
        counts = counts | {"photos": 0}
    parts = [
        f"{count} {noun}{'s' if count != 1 else ''}"
        for noun, count in (("photo", counts["photos"]), ("video", counts["videos"]))
        if count
    ]
    return ", ".join(parts) or "0 photos"


def _display_context(user):
    """What every listing needs to lay out and open its tiles."""
    viewer_prefs = get_module_settings(user, "files").get("viewer") or {}
    if not isinstance(viewer_prefs, dict):
        viewer_prefs = {}
    tile_size = _tile_size(user)
    return {
        "viewer_prefs": viewer_prefs,
        "tile": {"size": tile_size, "widths": TILE_WIDTHS},
        "tile_width": TILE_WIDTHS[tile_size - 1],
    }


def _url_with(params):
    base = reverse("photos_ui:index")
    return f"{base}?{urlencode(params)}" if params else base


def _faces_context(user):
    """What the shell needs to offer face grouping: the People entry, and
    whether the photo menus show their face rows."""
    return {
        "faces_available": faces_available(),
        "faces_enabled": faces_enabled(user),
        "people_url": reverse("photos_ui:people"),
    }


def _cluster_card(cluster):
    """What a page shows of a cluster: its cover, its size, where it links."""
    return {
        "uuid": str(cluster.pk),
        "hidden": cluster.hidden,
        "photo_count": cluster.photo_count,
        "cover_url": (
            reverse("photos-face-crop", kwargs={"pk": cluster.cover_id})
            if cluster.cover_id
            else None
        ),
        "url": _url_with({"cluster": str(cluster.pk)}),
    }


@login_required
@ensure_csrf_cookie
def index(request):
    """The timeline, opened at its newest photo or at ``?date=``.

    ``?cluster=`` narrows it to the photos of one face cluster: a person's
    page is their timeline.
    """
    cluster = _cluster(request)
    # Faces are only ever found in the user's personal photos.
    scope = MINE if cluster is not None else _scope(request)
    favorites, media_type, tag = _filters(request)
    tz = get_user_timezone(request.user)
    date_param = request.GET.get("date", "")
    position = START
    if date_param:
        try:
            position = parse_date_position(date_param, tz)
        except ValueError:
            return HttpResponseBadRequest("Invalid date.")

    view_files = _filtered_library(request.user, scope, favorites, tag, cluster)
    files = _of_type(view_files, media_type)
    counts = media_type_counts(view_files)
    scope_params = _scope_params(scope)
    type_params = _type_params(media_type)
    view_filter_params = _view_filter_params(favorites, tag, cluster)
    filter_params = view_filter_params | type_params
    view_params = scope_params | filter_params
    years = [
        {
            "label": str(year) if year is not None else "Undated",
            "count": count,
            "url": _url_with(
                view_params | {"date": str(year) if year is not None else UNDATED}
            ),
            "active": date_param == (str(year) if year is not None else UNDATED),
        }
        for year, count in year_counts(files, tz)
    ]

    if cluster is not None:
        active_view, title, icon = "people", "Unnamed person", "scan-face"
    elif tag is not None:
        active_view, title, icon = f"tag:{tag.uuid}", tag.name, tag.icon or "tag"
    elif favorites:
        active_view, title, icon = "favorites", "Favorites", "star"
    else:
        active_view, title, icon = "timeline", "Timeline", "images"

    context = {
        "active_view": active_view,
        "is_timeline_view": active_view == "timeline",
        "is_favorites_view": active_view == "favorites",
        "is_people_view": active_view == "people",
        "media_type": media_type,
        "title": title,
        "title_icon": icon,
        "cluster": _cluster_card(cluster) if cluster is not None else None,
        "count_label": _count_label(counts, media_type),
        "pending": (
            unanalyzed_count(request.user, scope)
            if active_view == "timeline" and media_type is None
            else 0
        ),
        "date_param": date_param,
        "latest_url": _url_with(view_params),
        "scope_tabs": (
            _scope_tabs(request.user, scope, filter_params) if cluster is None else []
        ),
        "type_tabs": _type_tabs(counts, media_type, scope_params | view_filter_params),
        "years": years,
        "tags": [
            {
                "tag": t,
                "url": _url_with(scope_params | {"tag": str(t.uuid)} | type_params),
                "active": tag is not None and t.pk == tag.pk,
            }
            for t in library_tags(request.user, scope)
        ],
        "timeline_url": _url_with(scope_params | type_params),
        "favorites_url": _url_with(scope_params | {"favorites": "1"} | type_params),
        "albums": _sidebar_albums(request.user),
        **_faces_context(request.user),
        **_display_context(request.user),
        **_timeline_page_context(request, files, position, tz, view_params),
    }
    return render(request, "photos/ui/index.html", context)


@login_required
def timeline(request):
    """The page after ``?cursor=``, appended to the grid by alpine-ajax."""
    cluster = _cluster(request)
    scope = MINE if cluster is not None else _scope(request)
    favorites, media_type, tag = _filters(request)
    try:
        position = parse_cursor(request.GET.get("cursor", ""))
    except ValueError:
        return HttpResponseBadRequest("Invalid cursor.")
    files = _of_type(
        _filtered_library(request.user, scope, favorites, tag, cluster), media_type
    )
    context = _timeline_page_context(
        request,
        files,
        position,
        get_user_timezone(request.user),
        _scope_params(scope)
        | _view_filter_params(favorites, tag, cluster)
        | _type_params(media_type),
    )
    return render(request, "photos/ui/partials/timeline_page.html", context)


@login_required
@ensure_csrf_cookie
def album(request, uuid):
    """An album, as the timeline shows it: by capture date or in its own order."""
    album = reachable_album(request.user, uuid)
    if album is None:
        raise Http404
    tz = get_user_timezone(request.user)
    files = album_files(request.user, album)
    card = album_cards(request.user, [album])[0]
    first, last = album_date_range(files)
    library_url = _url_with({})
    context = {
        "active_view": f"album:{album.uuid}",
        "title": album.title,
        "title_icon": "book-image",
        "count_label": _count_label(media_type_counts(files), None),
        "album": {
            "card": card,
            "manual": album.sort_mode == Album.SortMode.MANUAL,
            "date_range": _date_range_label(first, last, tz),
        },
        "album_data": {
            "uuid": str(album.uuid),
            "title": album.title,
            "description": album.description,
            "sort_mode": album.sort_mode,
        },
        "tags": [
            {"tag": t, "url": _url_with({"tag": str(t.uuid)}), "active": False}
            for t in library_tags(request.user, MINE)
        ],
        "timeline_url": library_url,
        "favorites_url": _url_with({"favorites": "1"}),
        "albums": _sidebar_albums(request.user, album),
        **_faces_context(request.user),
        **_display_context(request.user),
        **_album_page_context(request, album, files, None, tz),
    }
    return render(request, "photos/ui/index.html", context)


@login_required
def album_timeline(request, uuid):
    """The page of an album after ``?cursor=``, appended by alpine-ajax."""
    album = reachable_album(request.user, uuid)
    if album is None:
        raise Http404
    try:
        context = _album_page_context(
            request,
            album,
            album_files(request.user, album),
            request.GET.get("cursor", ""),
            get_user_timezone(request.user),
        )
    except ValueError:
        return HttpResponseBadRequest("Invalid cursor.")
    return render(request, "photos/ui/partials/timeline_page.html", context)


@login_required
@ensure_csrf_cookie
def people(request):
    """The People tab: the user's face clusters, or the way to turn them on."""
    if not faces_available():
        raise Http404
    show_hidden = is_truthy(request.GET.get("hidden"))
    enabled = faces_enabled(request.user)
    clusters = user_face_clusters(request.user)
    cards = [
        _cluster_card(cluster)
        for cluster in clusters.filter(hidden=show_hidden, photo_count__gt=0).order_by(
            "-photo_count", "-face_count", "created_at"
        )
    ]
    progress = face_progress(request.user) if enabled else None
    context = {
        "active_view": "people",
        "is_people_view": True,
        "title": "People",
        "tags": [
            {"tag": t, "url": _url_with({"tag": str(t.uuid)}), "active": False}
            for t in library_tags(request.user, MINE)
        ],
        "timeline_url": _url_with({}),
        "favorites_url": _url_with({"favorites": "1"}),
        "albums": _sidebar_albums(request.user),
        **_faces_context(request.user),
        **_display_context(request.user),
        "clusters": cards,
        "show_hidden": show_hidden,
        "hidden_count": (
            clusters.filter(hidden=True, photo_count__gt=0).count() if enabled else 0
        ),
        "people_hidden_url": f"{reverse('photos_ui:people')}?hidden=1",
        "progress": progress,
        "analyzing": progress is not None and progress["analyzed"] < progress["total"],
    }
    return render(request, "photos/ui/people.html", context)
