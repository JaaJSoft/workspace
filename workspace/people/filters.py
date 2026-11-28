"""The filters of the module's lists."""

from django import forms
from django_filters import rest_framework as filters

from workspace.common.filters import (
    KeyFilter,
    MultiValueFilter,
    OrderingFilter,
    TextSearchFilter,
)
from workspace.common.text import fold_text

from .models import Person, PersonList

_GROUP_HELP = (
    "The groups whose address books to read; `none` stands for the caller's "
    "own address book."
)


class PersonFilter(filters.FilterSet):
    q = filters.CharFilter(
        method="search", help_text="Text in names, organizations, emails or phones."
    )
    group = MultiValueFilter(
        child=forms.IntegerField(), allow_none=True, help_text=_GROUP_HELP
    )
    group_name = KeyFilter(field_name="group__name")
    list = MultiValueFilter(field_name="lists", child=forms.UUIDField(), distinct=True)
    list_name = KeyFilter(field_name="lists__name", distinct=True)
    ordering = OrderingFilter(
        fields={
            "display_name": "display_name",
            "created_at": "created_at",
            "updated_at": "updated_at",
        },
        tie_break=("uuid",),
    )

    class Meta:
        model = Person
        fields = []

    def search(self, queryset, name, value):
        # search_text holds the row folded: case and accents are gone from
        # both sides, and the column is the one the search index reads.
        needle = fold_text(value.strip())
        return queryset.filter(search_text__contains=needle) if needle else queryset


class PersonListFilter(filters.FilterSet):
    q = TextSearchFilter(fields=("name",))
    group = MultiValueFilter(
        child=forms.IntegerField(), allow_none=True, help_text=_GROUP_HELP
    )
    group_name = KeyFilter(field_name="group__name")

    class Meta:
        model = PersonList
        fields = []
