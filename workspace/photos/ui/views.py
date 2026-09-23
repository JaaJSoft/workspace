from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.common.booleans import is_truthy
from workspace.common.uuids import parse_uuid_or_none
from workspace.files.models import Tag
from workspace.photos.queries import (
    ALL,
    MINE,
    library_files,
    library_groups,
    library_tags,
    unanalyzed_count,
)
from workspace.photos.services.timeline import (
    START,
    UNDATED,
    parse_cursor,
    parse_date_position,
    timeline_page,
    with_timeline_fields,
    year_counts,
)
from workspace.users.services.settings import get_module_settings, get_user_timezone


def _scope(request):
    """The library ``?scope=`` asks for; 404 on a group the user is not in."""
    raw = request.GET.get("scope", "")
    if raw in ("", MINE):
        return MINE
    if raw == ALL:
        return ALL
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
    if scope == ALL:
        return {"scope": ALL}
    return {"scope": f"group:{scope.pk}"}


def _filters(request):
    """The active filters as ``(favorites, tag)``; 404 on a tag not the user's."""
    favorites = is_truthy(request.GET.get("favorites"))
    tag = None
    if request.GET.get("tag"):
        tag_uuid = parse_uuid_or_none(request.GET["tag"])
        if tag_uuid is not None:
            tag = Tag.objects.filter(owner=request.user, uuid=tag_uuid).first()
        if tag is None:
            raise Http404
    return favorites, tag


def _filtered_library(user, scope, favorites, tag):
    files = library_files(user, scope)
    if favorites:
        files = files.filter(favorites__owner=user)
    if tag is not None:
        files = files.filter(file_tags__tag=tag)
    return files


def _filter_params(favorites, tag):
    params = {}
    if favorites:
        params["favorites"] = "1"
    if tag is not None:
        params["tag"] = str(tag.uuid)
    return params


def _scope_tabs(user, scope, filter_params):
    """The library switcher: Mine, All, then one tab per group with photos.

    Hidden (empty) while there is nothing to switch to: a user whose groups
    hold no photo would only see two tabs showing the same pictures.
    """
    groups = list(library_groups(user))
    if not isinstance(scope, str) and scope not in groups:
        groups.append(scope)
    if not groups and scope == MINE:
        return []
    choices = [(MINE, "Mine", "user"), (ALL, "All", "layers")]
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


def _page_context(request, files, position, tz, view_params):
    page = timeline_page(with_timeline_fields(files, request.user), position, tz)
    next_url = None
    if page.next_cursor:
        params = view_params | {"cursor": page.next_cursor}
        next_url = f"{reverse('photos_ui:timeline')}?{urlencode(params)}"
    return {"entries": page.entries, "next_url": next_url}


def _url_with(params):
    base = reverse("photos_ui:index")
    return f"{base}?{urlencode(params)}" if params else base


@login_required
@ensure_csrf_cookie
def index(request):
    """The timeline, opened at its newest photo or at ``?date=``."""
    scope = _scope(request)
    favorites, tag = _filters(request)
    tz = get_user_timezone(request.user)
    date_param = request.GET.get("date", "")
    position = START
    if date_param:
        try:
            position = parse_date_position(date_param, tz)
        except ValueError:
            return HttpResponseBadRequest("Invalid date.")

    files = _filtered_library(request.user, scope, favorites, tag)
    scope_params = _scope_params(scope)
    filter_params = _filter_params(favorites, tag)
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

    if tag is not None:
        active_view, title, icon = f"tag:{tag.uuid}", tag.name, tag.icon or "tag"
    elif favorites:
        active_view, title, icon = "favorites", "Favorites", "star"
    else:
        active_view, title, icon = "timeline", "Timeline", "images"

    viewer_prefs = get_module_settings(request.user, "files").get("viewer") or {}
    if not isinstance(viewer_prefs, dict):
        viewer_prefs = {}

    context = {
        "active_view": active_view,
        "is_timeline_view": active_view == "timeline",
        "is_favorites_view": active_view == "favorites",
        "title": title,
        "title_icon": icon,
        "total": files.count(),
        "pending": (
            unanalyzed_count(request.user, scope) if active_view == "timeline" else 0
        ),
        "date_param": date_param,
        "latest_url": _url_with(view_params),
        "scope_tabs": _scope_tabs(request.user, scope, filter_params),
        "years": years,
        "tags": [
            {
                "tag": t,
                "url": _url_with(scope_params | {"tag": str(t.uuid)}),
                "active": tag is not None and t.pk == tag.pk,
            }
            for t in library_tags(request.user, scope)
        ],
        "timeline_url": _url_with(scope_params),
        "favorites_url": _url_with(scope_params | {"favorites": "1"}),
        "viewer_prefs": viewer_prefs,
        **_page_context(request, files, position, tz, view_params),
    }
    return render(request, "photos/ui/index.html", context)


@login_required
def timeline(request):
    """The page after ``?cursor=``, appended to the grid by alpine-ajax."""
    scope = _scope(request)
    favorites, tag = _filters(request)
    try:
        position = parse_cursor(request.GET.get("cursor", ""))
    except ValueError:
        return HttpResponseBadRequest("Invalid cursor.")
    files = _filtered_library(request.user, scope, favorites, tag)
    context = _page_context(
        request,
        files,
        position,
        get_user_timezone(request.user),
        _scope_params(scope) | _filter_params(favorites, tag),
    )
    return render(request, "photos/ui/partials/timeline_page.html", context)
