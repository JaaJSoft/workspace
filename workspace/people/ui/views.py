from itertools import groupby

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.exceptions import ValidationError

from workspace.common.uuids import parse_uuid_or_none
from workspace.people.queries import user_person_lists, user_persons
from workspace.people.serializers import parse_scope


def _letter(person):
    first = person.display_name[:1].upper()
    return first if first.isalpha() else "#"


def _filtered_persons(request):
    qs = user_persons(request.user).select_related("linked_user", "group")
    q = request.GET.get("q", "").strip().lower()
    if q:
        qs = qs.filter(search_text__contains=q)
    scope = request.GET.get("scope")
    if scope:
        try:
            qs = qs.filter(**parse_scope(request.user, scope))
        except ValidationError:
            pass
    list_uuid = parse_uuid_or_none(request.GET.get("list", ""))
    if list_uuid is not None:
        qs = qs.filter(lists__uuid=list_uuid)
    return qs.order_by("display_name", "uuid")


def _grouped(persons):
    return [(letter, list(items)) for letter, items in groupby(persons, key=_letter)]


def _list_context(request):
    persons = list(_filtered_persons(request))
    return {
        "groups_of_persons": _grouped(persons),
        "person_count": len(persons),
        "query": request.GET.get("q", ""),
        "scope": request.GET.get("scope", ""),
        "list_uuid": request.GET.get("list", ""),
    }


@login_required
@ensure_csrf_cookie
def index(request):
    context = _list_context(request)
    if request.headers.get("X-Alpine-Request"):
        return render(request, "people/ui/partials/person_list.html", context)
    lists = user_person_lists(request.user).annotate(member_count=Count("members"))
    context.update(
        {
            "lists_data": [
                {
                    "uuid": str(person_list.uuid),
                    "name": person_list.name,
                    "member_count": person_list.member_count,
                }
                for person_list in lists
            ],
            "user_groups": request.user.groups.order_by("name"),
            "panel_person_uuid": request.GET.get("person", ""),
        }
    )
    return render(request, "people/ui/index.html", context)
