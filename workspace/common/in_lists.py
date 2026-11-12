"""``field__in=values`` for a list of any size, on SQLite.

``__in`` binds one parameter per value, and SQLite refuses a statement
carrying more than its build allows: 32766 by default, 250000 on Debian's. A
list sized by the data (every message of a folder, every file of a tree) gets
there on a large account and nowhere else.

``compile_long_list`` is bound to Django's own ``In`` lookup as its SQLite
form, so it applies to every ``__in`` there is: filters, excludes, updates,
related fields and ``prefetch_related``. A long list goes to the database as
one parameter, a JSON array SQLite reads back with ``json_each``, and takes
the same indexes as the placeholders it replaces. Everything else compiles as
Django wrote it: a short list, a subquery, a composite key, and values JSON
would not hand back unchanged.

PostgreSQL needs none of this: Django inlines its parameters client side
there (the default, which this project keeps), so the statement binds nothing.
"""

import json

from django.db.models.expressions import ColPairs
from django.db.models.lookups import In

# A list keeps its placeholders while this many of its size still fit in one
# statement, so several short lists never add up to the limit.
_LISTS_PER_STATEMENT = 64

# What JSON carries to SQLite unchanged. A float is parsed again by SQLite's
# JSON reader, with no guarantee it lands on the exact value Python stored.
_JSON_SAFE_TYPES = frozenset({str, int})


def compile_long_list(lookup, compiler, connection):
    statement = _as_json_array(lookup, compiler, connection)
    return statement or lookup.as_sql(compiler, connection)


def _as_json_array(lookup, compiler, connection):
    """The single-parameter form of a long list, None when it has to keep
    Django's."""
    if not lookup.rhs_is_direct_value() or isinstance(lookup.lhs, ColPairs):
        return None
    longest_plain_list = connection.features.max_query_params // _LISTS_PER_STATEMENT
    if len(lookup.rhs) <= longest_plain_list:
        return None
    try:
        # Like Django's own form: NULL matches nothing, a repeat adds nothing.
        values = [value for value in dict.fromkeys(lookup.rhs) if value is not None]
    except TypeError:
        return None
    if not values:
        return None

    placeholders, params = lookup.batch_process_rhs(compiler, connection, values)
    if any(placeholder != "%s" for placeholder in placeholders) or not (
        {type(param) for param in params} <= _JSON_SAFE_TYPES
    ):
        return None

    lhs, lhs_params = lookup.process_lhs(compiler, connection)
    array = json.dumps(params, ensure_ascii=False)
    return f"{lhs} IN (SELECT value FROM json_each(%s))", (*lhs_params, array)


def bind_to_sqlite():
    In.as_sqlite = compile_long_list
