from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.common.booleans import is_truthy
from workspace.common.uuids import parse_uuid_or_none
from workspace.files.models import Tag
from workspace.photos.queries import library_files, library_tags, unanalyzed_count
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


def _filtered_library(user, favorites, tag):
    files = library_files(user)
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


def _page_context(request, files, position, tz, favorites, tag):
    page = timeline_page(with_timeline_fields(files, request.user), position, tz)
    next_url = None
    if page.next_cursor:
        params = _filter_params(favorites, tag) | {"cursor": page.next_cursor}
        next_url = f"{reverse('photos_ui:timeline')}?{urlencode(params)}"
    return {"entries": page.entries, "next_url": next_url}


def _url_with(params):
    base = reverse("photos_ui:index")
    return f"{base}?{urlencode(params)}" if params else base


@login_required
@ensure_csrf_cookie
def index(request):
    """The timeline, opened at its newest photo or at ``?date=``."""
    favorites, tag = _filters(request)
    tz = get_user_timezone(request.user)
    date_param = request.GET.get("date", "")
    position = START
    if date_param:
        try:
            position = parse_date_position(date_param, tz)
        except ValueError:
            return HttpResponseBadRequest("Invalid date.")

    files = _filtered_library(request.user, favorites, tag)
    filter_params = _filter_params(favorites, tag)
    years = [
        {
            "label": str(year) if year is not None else "Undated",
            "count": count,
            "url": _url_with(
                filter_params | {"date": str(year) if year is not None else UNDATED}
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
        active_view, title, icon = "all", "Timeline", "images"

    viewer_prefs = get_module_settings(request.user, "files").get("viewer") or {}
    if not isinstance(viewer_prefs, dict):
        viewer_prefs = {}

    context = {
        "active_view": active_view,
        "is_all_view": active_view == "all",
        "is_favorites_view": active_view == "favorites",
        "title": title,
        "title_icon": icon,
        "total": files.count(),
        "pending": unanalyzed_count(request.user) if active_view == "all" else 0,
        "date_param": date_param,
        "latest_url": _url_with(filter_params),
        "years": years,
        "tags": [
            {
                "tag": t,
                "url": _url_with({"tag": str(t.uuid)}),
                "active": tag is not None and t.pk == tag.pk,
            }
            for t in library_tags(request.user)
        ],
        "all_url": reverse("photos_ui:index"),
        "favorites_url": _url_with({"favorites": "1"}),
        "viewer_prefs": viewer_prefs,
        **_page_context(request, files, position, tz, favorites, tag),
    }
    return render(request, "photos/ui/index.html", context)


@login_required
def timeline(request):
    """The page after ``?cursor=``, appended to the grid by alpine-ajax."""
    favorites, tag = _filters(request)
    try:
        position = parse_cursor(request.GET.get("cursor", ""))
    except ValueError:
        return HttpResponseBadRequest("Invalid cursor.")
    files = _filtered_library(request.user, favorites, tag)
    context = _page_context(
        request, files, position, get_user_timezone(request.user), favorites, tag
    )
    return render(request, "photos/ui/partials/timeline_page.html", context)
