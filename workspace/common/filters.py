"""The filter types a module's ``FilterSet`` is made of.

Each one carries a rule every list of the API follows: a boolean has three
states and refuses anything else, a filter takes several values repeated or
separated by commas, ``none`` asks for the rows holding no value, ``q``
searches ignoring case and accents, a relation is also found by the name a
person gives it, ``ordering`` sorts on one declared field. A date range needs
nothing of ours: django-filter's ``DateFromToRangeFilter`` already reads
``<name>_after`` and ``<name>_before``.
"""

import django_filters
from django import forms
from django.db.models import F, OrderBy, Q
from django.db.models.functions import Lower
from drf_spectacular.drainage import set_override
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import filters as drf_filters
from rest_framework import serializers

# Asks a filter for the rows holding no value: ``assignee=none``.
NONE = "none"
# Asks a filter on a user for the caller's own rows: ``assignee=me``.
ME = "me"

_NO_VALUE = object()
_CALLER = object()


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

    def __init__(self, *, child, allow_none, allow_me, **kwargs):
        super().__init__(**kwargs)
        self.child = child
        self.words = {}
        if allow_none:
            self.words[NONE] = _NO_VALUE
        if allow_me:
            self.words[ME] = _CALLER

    def clean(self, value):
        return [
            self.words[part] if part in self.words else self.child.clean(part)
            for part in value
        ]


def without_none(values):
    """The values of a ``MultiValueFilter`` a ``method`` was handed, and
    whether the word ``none`` was among them."""
    return [value for value in values if value is not _NO_VALUE], _NO_VALUE in values


@extend_schema_field({"type": "array", "items": {"type": "string"}})
class MultiValueFilter(django_filters.Filter):
    """One filter, several values: ``tag=a&tag=b`` or ``tag=a,b``, matching
    the rows that hold any of them.

    *child* is the form field each value is read through
    (``forms.UUIDField()``, ``forms.IntegerField()``, a ``ChoiceField``); a
    value it refuses answers 400. With ``allow_none``, the word ``none``
    stands for the rows holding no value, alone or next to others. With
    ``allow_me``, on a filter whose values are user ids, the word ``me``
    stands for the caller. Pass ``distinct=True`` over a to-many relation,
    where a row matching two values would otherwise come back twice.
    """

    field_class = _ValuesField

    def __init__(self, *args, child=None, allow_none=False, allow_me=False, **kwargs):
        super().__init__(
            *args,
            child=child or forms.CharField(),
            allow_none=allow_none,
            allow_me=allow_me,
            **kwargs,
        )

    def _caller(self):
        request = getattr(self.parent, "request", None)
        return getattr(getattr(request, "user", None), "pk", None)

    def condition(self, values):
        """The rows holding one of *values*."""
        return Q(**{f"{self.field_name}__in": values})

    def filter(self, qs, value):
        if not value:
            return qs
        values = [self._caller() if part is _CALLER else part for part in value]
        # An anonymous caller has no id: ``me`` then names nobody.
        held = [part for part in values if part is not _NO_VALUE and part is not None]
        condition = self.condition(held) if held else Q(pk__in=[])
        if _NO_VALUE in values:
            condition |= Q(**{f"{self.field_name}__isnull": True})
        qs = self.get_method(qs)(condition)
        return qs.distinct() if self.distinct else qs


class KeyFilter(MultiValueFilter):
    """A relation found by what a person calls it: ``status_name=in progress``,
    ``assignee_username=alice``, ``project_key=WEB``.

    Sits next to the filter by identifier, so nobody has to look a uuid up
    first. The whole value must match, ignoring case and accents - a fragment
    of text is what ``q`` is for - and a value naming nothing the caller can
    see matches no row.
    """

    def condition(self, values):
        condition = Q()
        for value in values:
            condition |= Q(**{f"{self.field_name}__fiexact": value})
        return condition


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


class OrderingFilter(django_filters.ChoiceFilter):
    """``ordering=<field>``, or ``-<field>`` for the other way: one field.

    *fields* maps each name a caller may sort on to the column or the
    expression behind it; any other name answers 400. Empty values sort
    last either way. *tie_break* ends the order on columns that make it
    total, or two rows tied on the field could swap between two pages and one
    of them never be listed.
    """

    def __init__(self, *args, fields, tie_break=("pk",), **kwargs):
        self.sortable = dict(fields)
        self.tie_break = tuple(tie_break)
        names = [*self.sortable, *(f"-{name}" for name in self.sortable)]
        kwargs.setdefault("empty_label", None)
        super().__init__(*args, choices=[(name, name) for name in names], **kwargs)
        set_override(self, "field", {"type": "string", "enum": names})

    def filter(self, qs, value):
        if not value:
            return qs
        descending = value.startswith("-")
        column = self.sortable[value.removeprefix("-")]
        expression = F(column) if isinstance(column, str) else column
        return qs.order_by(
            OrderBy(expression, descending=descending, nulls_last=True),
            *self.tie_break,
        )


class CaseInsensitiveOrderingFilter(drf_filters.OrderingFilter):
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
