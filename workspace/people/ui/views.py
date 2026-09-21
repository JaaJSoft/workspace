import unicodedata
from itertools import groupby

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.exceptions import ValidationError

from workspace.common.uuids import parse_uuid_or_none
from workspace.people.actions import actions_for
from workspace.people.models import ADDRESS_TYPES, EMAIL_TYPES, PHONE_TYPES
from workspace.people.queries import (
    reachable_list,
    reachable_person,
    user_person_lists,
    user_persons,
)
from workspace.people.sections import render_sections
from workspace.people.serializers import PersonSerializer, parse_scope, scope_label

ENTRY_KINDS = [
    ("emails", "Emails", EMAIL_TYPES),
    ("phones", "Phones", PHONE_TYPES),
    ("addresses", "Addresses", ADDRESS_TYPES),
]


def _letter(person):
    """The bucket a person falls in: an accent-folded initial, or ``#``."""
    first = unicodedata.normalize("NFKD", person.display_name[:1])[:1].upper()
    return first if first.isalpha() else "#"


def _display_sort_key(person):
    # Bucketing and ordering must come from the same function, so the headers
    # can never repeat: a database collation sorts `Zoe` before `alain` on
    # SQLite and interleaves accents on PostgreSQL, either of which splits a
    # letter into several groups.
    letter = _letter(person)
    return (letter == "#", letter, person.display_name.casefold())


def _filtered_persons(request):
    """The matching persons, plus the filters that were actually applied.

    A scope or a list the user cannot reach is dropped rather than refused,
    and the caller echoes back only what survived - otherwise the page would
    reopen with a filter its own listing does not honour.
    """
    qs = user_persons(request.user).select_related("linked_user", "group")
    query = request.GET.get("q", "").strip()
    if query:
        qs = qs.filter(search_text__contains=query.lower())
    scope = request.GET.get("scope", "")
    if scope:
        try:
            qs = qs.filter(**parse_scope(request.user, scope))
        except ValidationError:
            scope = ""
    list_uuid = parse_uuid_or_none(request.GET.get("list", ""))
    person_list = reachable_list(request.user, list_uuid) if list_uuid else None
    if person_list is not None:
        qs = qs.filter(lists=person_list)
    applied = {
        "query": query,
        "scope": scope,
        "list_uuid": str(person_list.uuid) if person_list is not None else "",
    }
    return qs.order_by("display_name", "uuid"), applied


def _grouped(persons):
    return [(letter, list(items)) for letter, items in groupby(persons, key=_letter)]


def _list_context(request):
    qs, applied = _filtered_persons(request)
    persons = sorted(qs, key=_display_sort_key)
    return {
        "groups_of_persons": _grouped(persons),
        "person_count": len(persons),
        **applied,
    }


@login_required
@ensure_csrf_cookie
def index(request):
    context = _list_context(request)
    if request.headers.get("X-Alpine-Request"):
        return render(request, "people/ui/partials/person_list.html", context)
    lists = user_person_lists(request.user).annotate(member_count=Count("members"))
    panel_person = parse_uuid_or_none(request.GET.get("person", ""))
    context.update(
        {
            "lists_data": [
                {
                    "uuid": str(person_list.uuid),
                    "name": person_list.name,
                    "member_count": person_list.member_count,
                    "scope": scope_label(person_list),
                }
                for person_list in lists
            ],
            "groups_data": _groups_data(request.user),
            # The export dialog counts what a choice covers without a request.
            "mine_count": user_persons(request.user).filter(owner=request.user).count(),
            # Echoed into the page as the contact to open: a malformed value
            # would have the shell ask for a panel that can only 404, leaving
            # an empty panel open behind an error toast.
            "panel_person_uuid": str(panel_person or ""),
        }
    )
    return render(request, "people/ui/index.html", context)


def _groups_data(user):
    """Every group of the user with its contact count: the sidebar shows the
    ones holding contacts, the dialogs offer them all."""
    groups = user.groups.annotate(person_count=Count("persons")).order_by("name")
    return [
        {"id": group.id, "name": group.name, "person_count": group.person_count}
        for group in groups
    ]


@login_required
def person_panel(request, uuid):
    person = reachable_person(request.user, uuid)
    if person is None:
        raise Http404
    # A list only accepts members of its own scope, so offering the lists of
    # another scope would offer a membership the API refuses.
    lists = user_person_lists(request.user).filter(
        owner=person.owner, group=person.group
    )
    member_of = set(person.lists.values_list("uuid", flat=True))
    context = {
        "person_data": PersonSerializer(person, context={"request": request}).data,
        "actions": actions_for(request.user, [person])[str(person.uuid)],
        "lists_data": [
            {
                "uuid": str(person_list.uuid),
                "name": person_list.name,
                "member": person_list.uuid in member_of,
            }
            for person_list in lists
        ],
        "groups_data": _groups_data(request.user),
        "entry_kinds": ENTRY_KINDS,
        "sections": render_sections(request, request.user, person),
    }
    return render(request, "people/ui/partials/person_panel.html", context)
