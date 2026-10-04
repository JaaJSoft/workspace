from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest
from django.db.models import F
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import dateformat, timezone
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.common.booleans import is_truthy
from workspace.common.uuids import parse_uuid_or_none
from workspace.files.models import Tag
from workspace.files.services.filetype import get_viewer_by_slug
from workspace.files.ui.viewers import ViewerRegistry, render_viewer_panel
from workspace.notifications.services.notifications import mark_source_read
from workspace.people.queries import reachable_person
from workspace.photos.models import Album, Face, MediaItem
from workspace.photos.queries import (
    ALL,
    MINE,
    OWNER,
    SHARED,
    album_date_range,
    album_files,
    album_members,
    face_progress,
    get_album_role,
    has_shared_photos,
    hidden_folders,
    library_files,
    library_groups,
    library_tags,
    link_files,
    media_type_counts,
    reachable_album,
    unanalyzed_count,
    user_face_clusters,
)
from workspace.photos.services.album_cards import (
    album_cards,
    own_album_cards,
    shared_album_cards,
)
from workspace.photos.services.album_links import find_link, has_access, record_view
from workspace.photos.services.face_people import person_cards
from workspace.photos.services.face_preferences import faces_available, faces_enabled
from workspace.photos.services.face_review import (
    ALL_FACES,
    CONFIRMED,
    TO_CHECK,
    doubtful_faces,
    hidden_faces,
    person_faces,
    unassigned_groups,
    unnamed_count,
    unnamed_queue,
)
from workspace.photos.services.hidden import hidden_folder_data
from workspace.photos.services.import_folder import import_folder, import_folder_data
from workspace.photos.services.preferences import (
    ALL_TYPES,
    GROUP_SCOPE_PREFIX,
    default_media_type,
    default_scope_token,
    display_preferences,
    show_hidden,
)
from workspace.photos.services.timeline import (
    START,
    UNDATED,
    manual_page,
    mark_album_tiles,
    mark_favorite_toggles,
    parse_cursor,
    parse_date_position,
    parse_manual_cursor,
    timeline_page,
    with_album_fields,
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


@dataclass(frozen=True)
class _Defaults:
    """What the listing shows when the URL names no library or no media type:
    the user's preferences. A URL spells out only what differs from them."""

    scope: str
    media_type: object = None


def _defaults(user):
    return _Defaults(default_scope_token(user), default_media_type(user))


def _resolve_scope(user, token):
    """The library *token* names, or None when it names none the user has."""
    if token in (MINE, SHARED, ALL):
        return token
    prefix, _, group_id = token.partition(":")
    if f"{prefix}:" == GROUP_SCOPE_PREFIX and group_id.isdecimal():
        return user.groups.filter(pk=int(group_id)).first()
    return None


def _scope(request, defaults):
    """The library ``?scope=`` asks for, else the user's default one.

    404 on a group the user is not in. A default group the user has left
    falls back to their own photos instead: the preference is not the URL.
    """
    raw = request.GET.get("scope", "")
    if not raw:
        return _resolve_scope(request.user, defaults.scope) or MINE
    scope = _resolve_scope(request.user, raw)
    if scope is None:
        raise Http404
    return scope


def _view_scope(request, defaults, who, hidden):
    """The library a timeline reads. Faces are only ever found in the user's
    personal photos; what they hid may be anywhere they can open."""
    if who is not None:
        return MINE
    if hidden:
        return ALL
    return _scope(request, defaults)


def _scope_token(scope):
    if isinstance(scope, str):
        return scope
    return f"{GROUP_SCOPE_PREFIX}{scope.pk}"


def _scope_params(scope, defaults):
    token = _scope_token(scope)
    return {} if token == defaults.scope else {"scope": token}


def _filters(request, defaults):
    """The active filters as ``(favorites, media_type, tag)``.

    No ``?type=`` is the user's default media type, ``?type=all`` both kinds.
    404 on a tag not the user's, 400 on a ``?type=`` that is none of
    ``all``, ``photo`` and ``video``.
    """
    favorites = is_truthy(request.GET.get("favorites"))
    raw_type = request.GET.get("type", "")
    if not raw_type:
        media_type = defaults.media_type
    elif raw_type == ALL_TYPES:
        media_type = None
    elif raw_type in MediaItem.MediaType.values:
        media_type = MediaItem.MediaType(raw_type)
    else:
        raise BadRequest("Invalid media type.")
    tag = None
    if request.GET.get("tag"):
        tag_uuid = parse_uuid_or_none(request.GET["tag"])
        if tag_uuid is not None:
            tag = Tag.objects.filter(owner=request.user, uuid=tag_uuid).first()
        if tag is None:
            raise Http404
    return favorites, media_type, tag


@dataclass(frozen=True)
class _Who:
    """Whom a timeline is narrowed to: a named person, through every one of
    the user's clusters named after them, or one unnamed cluster."""

    clusters: tuple
    person: object = None

    @property
    def params(self):
        if self.person is not None:
            return {"person": str(self.person.pk)}
        return {"cluster": str(self.clusters[0].pk)}


def _who(request):
    """The ``?person=`` or ``?cluster=`` of the request, or None for neither.

    404 on a contact the user cannot see or whose face is in none of their
    clusters, and on a cluster not theirs.
    """
    clusters = user_face_clusters(request.user).select_related("person")
    if request.GET.get("person"):
        person_uuid = parse_uuid_or_none(request.GET["person"])
        person = (
            reachable_person(request.user, person_uuid)
            if person_uuid is not None
            else None
        )
        named = tuple(clusters.filter(person=person)) if person is not None else ()
        if not named:
            raise Http404
        return _Who(clusters=named, person=person)
    if request.GET.get("cluster"):
        cluster_uuid = parse_uuid_or_none(request.GET["cluster"])
        cluster = (
            clusters.filter(pk=cluster_uuid).first()
            if cluster_uuid is not None
            else None
        )
        if cluster is None:
            raise Http404
        return _Who(clusters=(cluster,))
    return None


def _filtered_library(user, scope, favorites, tag, who=None, hidden=False):
    """The library in *scope* narrowed by the sidebar view, every media type.

    *hidden* swaps the library for what the user hid from it.
    """
    files = library_files(user, scope, hidden=hidden)
    if favorites:
        files = files.filter(favorites__owner=user)
    if tag is not None:
        files = files.filter(file_tags__tag=tag)
    if who is not None:
        # A photo is never in two clusters of one person: no duplicates.
        files = files.filter(faces__cluster__in=[c.pk for c in who.clusters])
    return files


def _of_type(files, media_type):
    if media_type is None:
        return files
    return files.filter(media_item__media_type=media_type)


def _view_filter_params(favorites, tag, who=None, hidden=False):
    params = {}
    if hidden:
        params["hidden"] = "1"
    if favorites:
        params["favorites"] = "1"
    if tag is not None:
        params["tag"] = str(tag.uuid)
    if who is not None:
        params |= who.params
    return params


def _type_params(media_type, defaults):
    if media_type == defaults.media_type:
        return {}
    return {"type": media_type.value if media_type is not None else ALL_TYPES}


def _type_tabs(counts, media_type, params, defaults):
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
            "url": _url_with(params | _type_params(choice, defaults)),
            "active": choice == media_type,
        }
        for choice, label, title, icon in choices
    ]


def _scope_tabs(user, scope, filter_params, defaults):
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
            "url": _url_with(_scope_params(choice, defaults) | filter_params),
            "active": choice == scope,
        }
        for choice, label, icon in choices
    ]


def _paging(page, page_url, view_params=None):
    """The entries of *page* and the URL of the one after it, if any."""
    next_url = None
    if page.next_cursor:
        params = (view_params or {}) | {"cursor": page.next_cursor}
        next_url = f"{page_url}?{urlencode(params)}"
    return {"entries": page.entries, "next_url": next_url}


def _page_context(request, page, page_url, view_params=None):
    mark_favorite_toggles(page.photos, request.user)
    return _paging(page, page_url, view_params)


def _timeline_page_context(request, files, position, tz, view_params):
    page = timeline_page(with_timeline_fields(files, request.user), position, tz)
    return _page_context(request, page, reverse("photos_ui:timeline"), view_params)


def _album_page(album, files, cursor, tz):
    """The first page of *album*'s *files* when *cursor* is None, else the
    one after it, in the album's sort mode.

    ValueError on a cursor that is not one of the album's sort mode.
    """
    files = with_album_fields(files, album)
    if album.sort_mode == Album.SortMode.MANUAL:
        after = parse_manual_cursor(cursor) if cursor is not None else None
        return manual_page(files, after)
    position = parse_cursor(cursor) if cursor is not None else START
    return timeline_page(files, position, tz)


def _album_page_context(request, album, role, files, cursor, tz):
    """The page of *album* after *cursor* (the first for None), its tiles
    marked for the viewer's *role* (``timeline.mark_album_tiles``).

    ValueError on a cursor that is not one of the album's sort mode.
    """
    page = _album_page(album, with_timeline_fields(files, request.user), cursor, tz)
    mark_album_tiles(page.photos, request.user, album, role)
    page_url = reverse("photos_ui:album_timeline", args=[album.uuid])
    return _paging(page, page_url) | {
        "manual_order": album.sort_mode == Album.SortMode.MANUAL,
    }


def _sidebar_albums(user, active_album=None):
    """The sidebar's two album lists: the user's own, and the ones shared
    with them."""

    def items(cards):
        return [
            {
                "card": card,
                "url": reverse("photos_ui:album", args=[card.album.uuid]),
                "active": active_album is not None and card.album.pk == active_album.pk,
            }
            for card in cards
        ]

    return {
        "albums": items(own_album_cards(user)),
        "shared_albums": items(shared_album_cards(user)),
    }


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


def _hidden_nav_context(user, active=False):
    """The sidebar's Hidden entry: offered while the preference is on, and
    on the Hidden view itself whatever it says, to show where the user is."""
    return {
        "hidden_url": _url_with({"hidden": "1"}),
        "show_hidden_nav": active or show_hidden(user),
    }


def _faces_context(user):
    """What the shell needs to offer face grouping: the People entry, and
    whether the photo menus show their face rows."""
    return {
        "faces_available": faces_available(),
        "faces_enabled": faces_enabled(user),
        "people_url": reverse("photos_ui:people"),
    }


def _crop_url(face_id):
    return reverse("photos-face-crop", kwargs={"pk": face_id}) if face_id else None


def _person_ref(person):
    query = urlencode({"person": person.pk})
    return {
        "uuid": str(person.pk),
        "name": person.display_name,
        "url": f"{reverse('people_ui:index')}?{query}",
        "has_avatar": person.has_avatar,
    }


def _cluster_card(cluster):
    """What a page shows of an unnamed cluster, or of one cluster alone."""
    return {
        "uuid": str(cluster.pk),
        "person": _person_ref(cluster.person) if cluster.person_id else None,
        "clusters": [str(cluster.pk)],
        "hidden": cluster.hidden,
        "photo_count": cluster.photo_count,
        "cover_url": _crop_url(cluster.cover_id),
        "url": _url_with({"cluster": str(cluster.pk)}),
    }


def _person_card(card):
    """What a page shows of a named person: all their clusters, together.

    The cluster whose cover stands for the person comes first: it is the one
    whose cover "Use as contact photo" gives the contact.
    """
    ordered = [card.cover_cluster] + [
        c for c in card.clusters if c.pk != card.cover_cluster.pk
    ]
    return {
        "uuid": None,
        "person": _person_ref(card.person),
        "clusters": [str(c.pk) for c in ordered],
        "hidden": card.hidden,
        "photo_count": card.photo_count,
        "cover_url": _crop_url(card.cover_cluster.cover_id),
        "url": _url_with({"person": str(card.person.pk)}),
    }


def _who_card(who):
    if who.person is None:
        return _cluster_card(who.clusters[0])
    (card,) = person_cards(who.clusters)
    return _person_card(card)


def _scope_choices(user):
    """The libraries the timeline can open on, for the Preferences panel."""
    choices = [
        {"value": MINE, "label": "My photos"},
        {"value": ALL, "label": "All"},
        {"value": SHARED, "label": "Shared with me"},
    ]
    choices += [
        {"value": _scope_token(group), "label": group.name}
        for group in user.groups.order_by("name")
    ]
    return choices


def _render_page(request, template, context):
    """A photos page, or on an alpine-ajax request its two swap targets alone.

    Every swap on these pages aims at #photos-nav, #photos-content or an
    element inside them, and the fragment always carries both: alpine-ajax
    hands a GET still in flight to every identical one, whatever targets each
    caller asked for.

    The fragment is never stored: back and forward reuse a stored response
    without revalidating it, and this one must never come back as a page.
    """
    swap = bool(request.headers.get("X-Alpine-Request"))
    if swap:
        context["photos_shell"] = "photos/ui/swap.html"
    else:
        context["import_folder"] = import_folder_data(import_folder(request.user))
        context["photos_prefs"] = display_preferences(request.user)
        context["hidden_folders"] = [
            hidden_folder_data(folder) for folder in hidden_folders(request.user)
        ]
        context["scope_choices"] = _scope_choices(request.user)
    response = render(request, template, context)
    patch_vary_headers(response, ["X-Alpine-Request"])
    if swap:
        patch_cache_control(response, private=True, no_store=True)
    return response


@login_required
@ensure_csrf_cookie
def index(request):
    """The timeline, opened at its newest photo or at ``?date=``.

    ``?person=`` narrows it to the photos of one person, ``?cluster=`` to
    those of one unnamed face cluster: a person's page is their timeline.
    ``?hidden=1`` shows what the user hid from the library instead, and
    nothing else does.
    """
    who = _who(request)
    defaults = _defaults(request.user)
    hidden = who is None and is_truthy(request.GET.get("hidden"))
    scope = _view_scope(request, defaults, who, hidden)
    favorites, media_type, tag = _filters(request, defaults)
    tz = get_user_timezone(request.user)
    date_param = request.GET.get("date", "")
    position = START
    if date_param:
        try:
            position = parse_date_position(date_param, tz)
        except ValueError:
            return HttpResponseBadRequest("Invalid date.")

    view_files = _filtered_library(request.user, scope, favorites, tag, who, hidden)
    files = _of_type(view_files, media_type)
    counts = media_type_counts(view_files)
    scope_params = {} if hidden else _scope_params(scope, defaults)
    type_params = _type_params(media_type, defaults)
    view_filter_params = _view_filter_params(favorites, tag, who, hidden)
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

    if who is not None:
        active_view, icon = "people", "scan-face"
        title = who.person.display_name if who.person else "Unnamed person"
    elif hidden:
        active_view, title, icon = "hidden", "Hidden", "eye-off"
    elif tag is not None:
        active_view, title, icon = f"tag:{tag.uuid}", tag.name, tag.icon or "tag"
    elif favorites:
        active_view, title, icon = "favorites", "Favorites", "star"
    else:
        active_view, title, icon = "timeline", "Timeline", "images"

    context = {
        "person_views": _person_views(who, "photos") if who is not None else None,
        "active_view": active_view,
        "is_timeline_view": active_view == "timeline",
        "is_favorites_view": active_view == "favorites",
        "is_people_view": active_view == "people",
        "is_hidden_view": hidden,
        "media_type": media_type,
        "title": title,
        "title_icon": icon,
        "cluster": _who_card(who) if who is not None else None,
        "count_label": _count_label(counts, media_type),
        "view_empty": not (counts["photos"] or counts["videos"]),
        "pending": (
            unanalyzed_count(request.user, scope)
            if active_view == "timeline" and media_type is None
            else 0
        ),
        "date_param": date_param,
        "latest_url": _url_with(view_params),
        "scope_tabs": (
            _scope_tabs(request.user, scope, filter_params, defaults)
            if who is None and not hidden
            else []
        ),
        "type_tabs": _type_tabs(
            counts, media_type, scope_params | view_filter_params, defaults
        ),
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
        **_sidebar_albums(request.user),
        **_hidden_nav_context(request.user, active=hidden),
        **_faces_context(request.user),
        **_display_context(request.user),
        **_timeline_page_context(request, files, position, tz, view_params),
    }
    return _render_page(request, "photos/ui/index.html", context)


@login_required
def timeline(request):
    """The page after ``?cursor=``, appended to the grid by alpine-ajax."""
    who = _who(request)
    defaults = _defaults(request.user)
    hidden = who is None and is_truthy(request.GET.get("hidden"))
    scope = _view_scope(request, defaults, who, hidden)
    favorites, media_type, tag = _filters(request, defaults)
    try:
        position = parse_cursor(request.GET.get("cursor", ""))
    except ValueError:
        return HttpResponseBadRequest("Invalid cursor.")
    files = _of_type(
        _filtered_library(request.user, scope, favorites, tag, who, hidden),
        media_type,
    )
    context = _timeline_page_context(
        request,
        files,
        position,
        get_user_timezone(request.user),
        ({} if hidden else _scope_params(scope, defaults))
        | _view_filter_params(favorites, tag, who, hidden)
        | _type_params(media_type, defaults),
    )
    return render(request, "photos/ui/partials/timeline_page.html", context)


# Members a shared album's header shows by their avatar; the rest are a count.
HEADER_MEMBERS = 5


def _album_sharing(album, role):
    """Who an album page says the album is shared with, or None for an
    album nobody else can open: its members' first avatars and their
    count, and who shared it when the viewer was invited."""
    if album.group_id is None and not album.shares.exists():
        return None
    members = album_members(album).order_by("username")
    return {
        "members": list(members[:HEADER_MEMBERS]),
        "count": members.count(),
        "shared_by": album.owner if role != OWNER and album.group_id is None else None,
        "role": role,
    }


@login_required
@ensure_csrf_cookie
def album(request, uuid):
    """An album, as the timeline shows it: by capture date or in its own order.

    Opening it reads the notifications about it: the invitation and the
    photos added since.
    """
    album = reachable_album(request.user, uuid)
    if album is None:
        raise Http404
    role = get_album_role(request.user, album)
    mark_source_read(request.user, album)
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
            "sharing": _album_sharing(album, role),
        },
        "album_data": {
            "uuid": str(album.uuid),
            "title": album.title,
            "description": album.description,
            "sort_mode": album.sort_mode,
            "role": role,
            "allow_download": album.allow_download,
        },
        "tags": [
            {"tag": t, "url": _url_with({"tag": str(t.uuid)}), "active": False}
            for t in library_tags(request.user, MINE)
        ],
        "timeline_url": library_url,
        "favorites_url": _url_with({"favorites": "1"}),
        **_sidebar_albums(request.user, album),
        **_hidden_nav_context(request.user),
        **_faces_context(request.user),
        **_display_context(request.user),
        **_album_page_context(request, album, role, files, None, tz),
    }
    return _render_page(request, "photos/ui/index.html", context)


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
            get_album_role(request.user, album),
            album_files(request.user, album),
            request.GET.get("cursor", ""),
            get_user_timezone(request.user),
        )
    except ValueError:
        return HttpResponseBadRequest("Invalid cursor.")
    return render(request, "photos/ui/partials/timeline_page.html", context)


def _viewer_html(request, file_obj, content_url):
    """*file_obj* in the Files viewer that fits it, read-only, its bytes
    fetched from *content_url*; empty when no viewer fits it."""
    if not file_obj.is_viewable():
        return ""
    viewer_class = get_viewer_by_slug(file_obj.viewer) or ViewerRegistry.get_viewer(
        file_obj.type, file_obj.name
    )
    if viewer_class is None:
        return ""
    viewer = viewer_class(file_obj)
    viewer._user_can_edit = False
    viewer._content_url = content_url
    return viewer.render(request)


@login_required
def album_viewer(request, uuid, file_uuid):
    """The Files viewer panel for a photo of an album the viewer cannot open
    in Files: read-only, its bytes served through the album."""
    album = reachable_album(request.user, uuid)
    if album is None:
        raise Http404
    file_obj = album_files(request.user, album).filter(uuid=file_uuid).first()
    if file_obj is None:
        raise Http404
    html = _viewer_html(
        request,
        file_obj,
        reverse("photo-album-file-content", args=[album.uuid, file_obj.uuid]),
    )
    if not html:
        return HttpResponse(
            render_viewer_panel(
                '<div class="p-8 text-center text-error">No preview for this file</div>'
            ),
            status=400,
        )
    return HttpResponse(render_viewer_panel(html))


def _link_url(name, link, access_token, *args, **params):
    """The URL *name* for *link*, carrying the visitor's access token."""
    if access_token:
        params["access_token"] = access_token
    url = reverse(name, args=[link.token, *args])
    return f"{url}?{urlencode(params)}" if params else url


def _shared_tiles(link, access_token, photos):
    """Point each photo of a linked album's page at its thumbnail and at the
    page that shows it, both through the link."""
    for photo in photos:
        photo.shared_thumbnail_url = _link_url(
            "photo-album-link-thumbnail", link, access_token, photo.uuid
        )
        photo.shared_view_url = _link_url(
            "photos_ui:shared_album", link, access_token, photo=photo.uuid
        )


def _album_tz(album):
    """The time zone a linked album's days are cut in: its owner's, since a
    visitor has none."""
    return get_user_timezone(album.owner)


def _shared_page_context(link, access_token, cursor):
    """The page of the linked album after *cursor* (the first for None).

    ValueError on a malformed cursor.
    """
    album = link.album
    files = link_files(album).select_related("media_item", "media_info")
    page = _album_page(album, files, cursor, _album_tz(album))
    _shared_tiles(link, access_token, page.photos)
    page_url = reverse("photos_ui:shared_album_timeline", args=[link.token])
    params = {"access_token": access_token} if access_token else {}
    return _paging(page, page_url, params) | {
        "tile_template": "photos/ui/partials/shared_tile.html",
    }


def _shared_neighbours(link, access_token, files, file_obj):
    """Where the photos around *file_obj* are, in the album's order, as
    ``(previous_url, next_url, position, total)``; the URLs are empty at
    either end."""
    album = link.album
    if album.sort_mode == Album.SortMode.MANUAL:
        ordered = with_album_fields(files, album).order_by("album_position", "uuid")
    else:
        ordered = files.order_by(
            F("media_item__taken_at").desc(nulls_last=True), "-created_at", "-uuid"
        )
    uuids = list(ordered.values_list("uuid", flat=True))
    index = uuids.index(file_obj.uuid)

    def url(i):
        if 0 <= i < len(uuids):
            return _link_url(
                "photos_ui:shared_album", link, access_token, photo=uuids[i]
            )
        return ""

    return url(index - 1), url(index + 1), index + 1, len(uuids)


def _shared_photo_context(request, link, access_token, files, file_obj):
    previous_url, next_url, position, total = _shared_neighbours(
        link, access_token, files, file_obj
    )
    download_url = ""
    if link.allow_download:
        download_url = _link_url(
            "photo-album-link-download", link, access_token, file_obj.uuid
        )
    content_url = _link_url(
        "photo-album-link-content", link, access_token, file_obj.uuid
    )
    return {
        "file": file_obj,
        "viewer_html": _viewer_html(request, file_obj, content_url),
        "download_url": download_url,
        "previous_url": previous_url,
        "next_url": next_url,
        "position": position,
        "total": total,
    }


@ensure_csrf_cookie
def shared_album(request, token):
    """The page a public album link opens, without an account.

    The grid of the album, or with ``?photo=`` one of its photos in the
    viewer, between the ones before and after it. Going from one to the
    other swaps ``#shared-album-content`` alone.
    """
    link = find_link(token)
    if link is None:
        raise Http404
    if link.is_expired:
        return render(request, "photos/ui/shared_album.html", {"expired": True})
    access_token = request.GET.get("access_token", "")
    if not has_access(link, access_token):
        return render(
            request,
            "photos/ui/shared_album.html",
            {
                "needs_password": True,
                "share_token": token,
                "verify_url": reverse("photo-album-link-verify", args=[token]),
                "page_url": reverse("photos_ui:shared_album", args=[token]),
            },
        )

    album = link.album
    files = link_files(album)
    first, last = album_date_range(files)
    context = {
        "link": link,
        "album": album,
        "access_token": access_token,
        "count_label": _count_label(media_type_counts(files), None),
        "date_range": _date_range_label(first, last, _album_tz(album)),
        "album_url": _link_url("photos_ui:shared_album", link, access_token),
        "archive_url": (
            _link_url("photo-album-link-archive", link, access_token)
            if link.allow_download
            else ""
        ),
    }
    if request.GET.get("photo"):
        photo_uuid = parse_uuid_or_none(request.GET["photo"])
        file_obj = None
        if photo_uuid is not None:
            file_obj = (
                files.select_related("media_item").filter(uuid=photo_uuid).first()
            )
        if file_obj is None:
            raise Http404
        context["photo"] = _shared_photo_context(
            request, link, access_token, files, file_obj
        )
    else:
        context.update(_shared_page_context(link, access_token, None))

    if request.headers.get("X-Alpine-Request"):
        response = render(
            request, "photos/ui/shared_album.html#shared-album-content", context
        )
        patch_vary_headers(response, ["X-Alpine-Request"])
        patch_cache_control(response, private=True, no_store=True)
        return response
    record_view(link)
    response = render(request, "photos/ui/shared_album.html", context)
    patch_vary_headers(response, ["X-Alpine-Request"])
    return response


def shared_album_timeline(request, token):
    """The page of a linked album after ``?cursor=``, appended as the
    visitor scrolls."""
    link = find_link(token)
    if link is None or link.is_expired:
        raise Http404
    access_token = request.GET.get("access_token", "")
    if not has_access(link, access_token):
        raise Http404
    try:
        context = _shared_page_context(
            link, access_token, request.GET.get("cursor", "")
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
    clusters = list(
        user_face_clusters(request.user)
        .filter(photo_count__gt=0)
        .select_related("person")
        .order_by("-photo_count", "-face_count", "created_at")
    )
    persons = person_cards(clusters)
    named = [_person_card(card) for card in persons if card.hidden == show_hidden]
    unnamed = [
        _cluster_card(cluster)
        for cluster in clusters
        if cluster.person_id is None and cluster.hidden == show_hidden
    ]
    hidden = hidden_faces(request.user)
    hidden_count = (
        sum(card.hidden for card in persons)
        + sum(c.hidden for c in clusters if c.person_id is None)
        + hidden.total
    )
    progress = face_progress(request.user) if enabled else None
    context = {
        **_people_shell_context(request.user),
        "named": named,
        "unnamed": unnamed,
        "people_count": len(named) + len(unnamed),
        "unnamed_count": sum(not c.hidden for c in clusters if c.person_id is None),
        "show_hidden": show_hidden,
        "hidden_faces": (
            {"faces": _face_items(hidden.face_ids), "total": hidden.total}
            if show_hidden
            else None
        ),
        "hidden_count": hidden_count if enabled else 0,
        "people_hidden_url": f"{reverse('photos_ui:people')}?hidden=1",
        "progress": progress,
        "analyzing": progress is not None and progress["analyzed"] < progress["total"],
    }
    return _render_page(request, "photos/ui/people.html", context)


def _people_shell_context(user):
    """The sidebar and shell of a page under the People tab."""
    return {
        "active_view": "people",
        "is_people_view": True,
        "title": "People",
        "tags": [
            {"tag": t, "url": _url_with({"tag": str(t.uuid)}), "active": False}
            for t in library_tags(user, MINE)
        ],
        "timeline_url": _url_with({}),
        "favorites_url": _url_with({"favorites": "1"}),
        **_sidebar_albums(user),
        "review_url": reverse("photos_ui:people_review"),
        **_hidden_nav_context(user),
        **_faces_context(user),
        **_display_context(user),
    }


def _person_summary(card):
    return {
        "uuid": str(card.person.pk),
        "name": card.person.display_name,
        "cover_url": _crop_url(card.cover_cluster.cover_id),
        "photo_count": card.photo_count,
    }


def _face_items(face_ids):
    """What a board shows of each of *face_ids*, in that order: the crop, and
    the photo or the moment of the video it was found in, for the tile to
    open it."""
    rows = {
        row[0]: row
        for row in Face.objects.filter(pk__in=face_ids).values_list(
            "pk", "file_id", "file__name", "file__type", "assignment", "timestamp"
        )
    }
    return [
        {
            "uuid": str(pk),
            "crop_url": _crop_url(pk),
            "file": str(rows[pk][1]),
            "file_name": rows[pk][2],
            "file_type": rows[pk][3],
            "assignment": rows[pk][4],
            "timestamp": rows[pk][5],
        }
        for pk in face_ids
        if pk in rows
    ]


# Cards the review page holds at once; the next ones come after a reload.
REVIEW_CARDS = 30
# Among cards of the same weight, the named people's doubts first.
_REVIEW_ORDER = {"check": 0, "cluster": 1, "loose": 2}


def _review_cards(user):
    """What the review page asks about, one card at a time: a group of faces
    and who they may be. The unnamed clusters, the faces the grouping put
    under a named person without being sure, and the look-alike groups of
    faces in no cluster - the cards settling the most photos first."""
    cards = [
        {
            "kind": "cluster",
            "key": f"cluster-{item.cluster.pk}",
            "cluster": str(item.cluster.pk),
            "guess": _person_summary(item.suggestion) if item.suggestion else None,
            "cover_url": _crop_url(item.cluster.cover_id),
            "url": _url_with({"cluster": str(item.cluster.pk)}),
            "photo_count": item.cluster.photo_count,
            "total": item.cluster.face_count,
            "face_ids": item.face_ids,
            "weight": item.cluster.photo_count,
        }
        for item in unnamed_queue(user, limit=REVIEW_CARDS)
    ]
    for doubts in doubtful_faces(user):
        person = _person_summary(doubts.card)
        cards.append(
            {
                "kind": "check",
                "key": f"check-{person['uuid']}",
                "cluster": None,
                "guess": person,
                "cover_url": person["cover_url"],
                "url": _url_with({"person": person["uuid"]}),
                "photo_count": person["photo_count"],
                "total": doubts.total,
                "face_ids": tuple(face.face_id for face in doubts.faces),
                "weight": doubts.total,
            }
        )
    for group in unassigned_groups(user):
        cards.append(
            {
                "kind": "loose",
                "key": f"loose-{group.face_ids[0]}",
                "cluster": None,
                "guess": _person_summary(group.suggestion)
                if group.suggestion
                else None,
                "cover_url": _crop_url(group.face_ids[0]),
                "url": None,
                "photo_count": None,
                "total": len(group.face_ids),
                "face_ids": group.face_ids,
                "weight": len(group.face_ids),
            }
        )
    cards.sort(key=lambda card: (-card["weight"], _REVIEW_ORDER[card["kind"]]))
    # The unnamed clusters past the first page were not read.
    left = len(cards) + max(0, unnamed_count(user) - REVIEW_CARDS)
    cards = cards[:REVIEW_CARDS]
    faces = {
        UUID(item["uuid"]): item
        for item in _face_items([pk for card in cards for pk in card["face_ids"]])
    }
    for card in cards:
        face_ids = card.pop("face_ids")
        del card["weight"]
        card["faces"] = [faces[pk] for pk in face_ids if pk in faces]
    return cards, left


@login_required
@ensure_csrf_cookie
def people_review(request):
    """Naming people and checking faces one card after the other."""
    if not faces_available():
        raise Http404
    if not faces_enabled(request.user):
        # The opt-in card lives on the People tab.
        return redirect("photos_ui:people")
    cards, left = _review_cards(request.user)
    context = {
        **_people_shell_context(request.user),
        "review": {"cards": cards, "left": left},
    }
    return _render_page(request, "photos/ui/people_review.html", context)


def _person_views(who, active):
    """The Photos and Faces tabs of a person's page."""
    faces_url = f"{reverse('photos_ui:person_faces')}?{urlencode(who.params)}"
    return [
        {
            "label": "Photos",
            "icon": "images",
            "url": _url_with(who.params),
            "active": active == "photos",
        },
        {
            "label": "Faces",
            "icon": "scan-face",
            "url": faces_url,
            "active": active == "faces",
        },
    ]


@login_required
@ensure_csrf_cookie
def person_faces_view(request):
    """A person's faces rather than their photos, to pick the ones that are
    someone else. Same ``?person=`` or ``?cluster=`` as their timeline;
    ``?show=check`` or ``?show=confirmed`` narrows the faces."""
    if not faces_available():
        raise Http404
    if not faces_enabled(request.user):
        return redirect("photos_ui:people")
    who = _who(request)
    if who is None:
        raise Http404
    show = request.GET.get("show")
    if show not in (ALL_FACES, TO_CHECK, CONFIRMED):
        show = ALL_FACES
    page = person_faces(request.user, who.clusters, show)
    base = f"{reverse('photos_ui:person_faces')}?{urlencode(who.params)}"
    context = {
        **_people_shell_context(request.user),
        "title": who.person.display_name if who.person else "Unnamed person",
        "cluster": _who_card(who),
        "person_views": _person_views(who, "faces"),
        "board": {"faces": _face_items(page.face_ids), "total": page.total},
        "face_count": page.total,
        "shows": [
            {
                "label": label,
                "url": base if value == ALL_FACES else f"{base}&show={value}",
                "active": value == show,
            }
            for value, label in (
                (ALL_FACES, "All"),
                (TO_CHECK, "Not confirmed"),
                (CONFIRMED, "Confirmed"),
            )
        ],
    }
    return _render_page(request, "photos/ui/person_faces.html", context)
