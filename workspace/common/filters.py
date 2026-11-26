"""The filter types a module's ``FilterSet`` is made of.

Each one carries a rule every list of the API follows: a boolean has three
states and refuses anything else, a filter takes several values repeated or
separated by commas, ``none`` asks for the rows holding no value, ``q``
searches ignoring case and accents. A date range needs nothing of ours:
django-filter's ``DateFromToRangeFilter`` already reads ``<name>_after`` and
``<name>_before``.
"""

import django_filters
from django import forms
from django.db.models import Q
from django.db.models.functions import Lower
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.filters import OrderingFilter

# Asks a filter for the rows holding no value: ``assignee=none``.
NONE = "none"


class _BooleanField(forms.Field):
    def to_python(self, value):
        if value in self.empty_values:
            return None
        try:
            return serializers.BooleanField().run_validation(value)
        except serializers.ValidationError as exc:
            raise forms.ValidationError(
                [str(message) for message in exc.detail]
            ) from exc


@extend_schema_field(OpenApiTypes.BOOL)
class BooleanFilter(django_filters.Filter):
    """Absent is no filter, ``true`` and ``false`` each keep their half, and
    any other value is refused.

    django-filter's own takes an unreadable value for an absent one, so a
    typo lists every row instead of answering 400. For a state stored as a
    nullable column, name the column and negate the null test:
    ``BooleanFilter(field_name="read_at", lookup_expr="isnull", exclude=True)``.
    """

    field_class = _BooleanField


class _ValuesWidget(forms.TextInput):
    def value_from_datadict(self, data, files, name):
        given = data.getlist(name) if hasattr(data, "getlist") else [data.get(name)]
        return [
            part.strip()
            for value in given
            if value
            for part in str(value).split(",")
            if part.strip()
        ]

    def format_value(self, value):
        if isinstance(value, list):
            value = ",".join(value)
        return super().format_value(value)


class _ValuesField(forms.Field):
    widget = _ValuesWidget

    def __init__(self, *, child, allow_none, **kwargs):
        super().__init__(**kwargs)
        self.child = child
        self.allow_none = allow_none

    def clean(self, value):
        return [
            None if self.allow_none and part == NONE else self.child.clean(part)
            for part in value
        ]


@extend_schema_field({"type": "array", "items": {"type": "string"}})
class MultiValueFilter(django_filters.Filter):
    """One filter, several values: ``tag=a&tag=b`` or ``tag=a,b``, matching
    the rows that hold any of them.

    *child* is the form field each value is read through
    (``forms.UUIDField()``, ``forms.IntegerField()``, a ``ChoiceField``); a
    value it refuses answers 400. With ``allow_none``, the word ``none``
    stands for the rows holding no value, alone or next to others. Pass
    ``distinct=True`` over a to-many relation, where a row matching two values
    would otherwise come back twice.
    """

    field_class = _ValuesField

    def __init__(self, *args, child=None, allow_none=False, **kwargs):
        super().__init__(
            *args, child=child or forms.CharField(), allow_none=allow_none, **kwargs
        )

    def filter(self, qs, value):
        if not value:
            return qs
        held = [part for part in value if part is not None]
        condition = Q(**{f"{self.field_name}__in": held}) if held else Q()
        if len(held) < len(value):
            condition |= Q(**{f"{self.field_name}__isnull": True})
        qs = self.get_method(qs)(condition)
        return qs.distinct() if self.distinct else qs


class TextSearchFilter(django_filters.CharFilter):
    """``q``: the rows where one of *fields* contains the text, ignoring case
    and accents. For a list backed by a full-text index, write a filter over
    that index instead.
    """

    def __init__(self, *args, fields, **kwargs):
        super().__init__(*args, **kwargs)
        self.search_fields = fields

    def filter(self, qs, value):
        if not value:
            return qs
        condition = Q()
        for name in self.search_fields:
            condition |= Q(**{f"{name}__ficontains": value})
        qs = self.get_method(qs)(condition)
        return qs.distinct() if self.distinct else qs


class CaseInsensitiveOrderingFilter(OrderingFilter):
    """OrderingFilter that wraps text fields in ``Lower()`` for case-insensitive sort.

    Set ``ordering_case_insensitive_fields`` on the view to list which fields
    should be lowered. Defaults to all ``ordering_fields`` when not set.
    """

    def get_ordering(self, request, queryset, view):
        ordering = super().get_ordering(request, queryset, view)
        if not ordering:
            return ordering
        ci_fields = set(
            getattr(view, "ordering_case_insensitive_fields", None)
            or getattr(view, "ordering_fields", [])
        )
        result = []
        for field in ordering:
            if not isinstance(field, str):
                result.append(field)
                continue
            bare = field.lstrip("-")
            if bare in ci_fields:
                expr = Lower(bare)
                result.append(expr.desc() if field.startswith("-") else expr)
            else:
                result.append(field)
        return result
