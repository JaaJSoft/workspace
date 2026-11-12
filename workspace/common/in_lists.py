"""``field__in=values`` for a list of any size.

``__in`` binds one parameter per value, and SQLite refuses a statement
carrying more than its build allows: 32766 by default, 250000 on Debian's. A
list sized by the data (every message of a folder, every file of a tree) gets
there on a large account and nowhere else.

Django's own ``In`` lookup gets a form of its own on each database, so this
applies to every ``__in`` there is: filters, excludes, updates, related fields
and ``prefetch_related``. A long list goes to the database as one parameter
and takes the same indexes as the placeholders it replaces:

- SQLite reads it back out of a JSON array (``json_each``);
- PostgreSQL compares against an array (``= ANY``). It has no limit to stay
  under while Django inlines the parameters client side, the default this
  project keeps, but one array is cheaper for the driver to format and for
  the server to plan than thousands of placeholders.

Everything else compiles as Django wrote it: a short list, a subquery, a
composite key, and values the single parameter would not carry unchanged.
"""

import json
from uuid import UUID

from django.db.models.expressions import ColPairs
from django.db.models.lookups import In
from psycopg.types.numeric import Int2, Int4, Int8

# A list keeps its placeholders while this many of its size still fit in one
# statement, so several short lists never add up to the limit.
_LISTS_PER_STATEMENT = 64

# What PostgreSQL's wire protocol binds at most. Client-side binding lifts it,
# and the threshold still has to come from somewhere.
_POSTGRESQL_PARAMETER_LIMIT = 2**16 - 1

# What JSON carries to SQLite unchanged. A float is parsed again by SQLite's
# JSON reader, with no guarantee it lands on the exact value Python stored.
_JSON_SAFE_TYPES = frozenset({str, int})

_POSTGRESQL_TEXT_TYPES = ("varchar", "text")

# The sized integers Django wraps a value in, after the column it is for. An
# array of them takes that size, where a lone placeholder is a bare number.
_POSTGRESQL_INTEGER_BITS = {Int2: 16, Int4: 32, Int8: 64}


def as_sqlite(lookup, compiler, connection):
    values = _long_list(lookup, compiler, connection)
    if values is None or not {type(value) for value in values} <= _JSON_SAFE_TYPES:
        return lookup.as_sql(compiler, connection)
    lhs, lhs_params = lookup.process_lhs(compiler, connection)
    array = json.dumps(values, ensure_ascii=False)
    return f"{lhs} IN (SELECT value FROM json_each(%s))", (*lhs_params, array)


def as_postgresql(lookup, compiler, connection):
    values = _long_list(lookup, compiler, connection)
    if values is None or not _fits_one_array(lookup, values, connection):
        return lookup.as_sql(compiler, connection)
    lhs, lhs_params = lookup.process_lhs(compiler, connection)
    return f"{lhs} = ANY(%s)", (*lhs_params, values)


def _long_list(lookup, compiler, connection):
    """The prepared values of a long single-column list, None for anything
    that has to keep Django's form."""
    if not lookup.rhs_is_direct_value() or isinstance(lookup.lhs, ColPairs):
        return None
    limit = connection.features.max_query_params or _POSTGRESQL_PARAMETER_LIMIT
    if len(lookup.rhs) <= limit // _LISTS_PER_STATEMENT:
        return None
    try:
        # Like Django's own form: NULL matches nothing, a repeat adds nothing.
        values = [value for value in dict.fromkeys(lookup.rhs) if value is not None]
    except TypeError:
        return None
    if not values:
        return None
    placeholders, params = lookup.batch_process_rhs(compiler, connection, values)
    if any(placeholder != "%s" for placeholder in placeholders):
        return None
    return list(params)


def _fits_one_array(lookup, values, connection):
    """Whether the driver sends these values as an array the column compares
    with.

    An array is typed by its elements, where a lone placeholder takes the
    type of the column. So the values share one type, and text only goes to a
    text column: an inet or a date column given text[] has no operator to
    compare with.
    """
    types = {type(value) for value in values}
    if len(types) != 1:
        return False
    (value_type,) = types
    if value_type is UUID:
        return True
    if value_type is str:
        column_type = lookup.lhs.output_field.db_type(connection) or ""
        return column_type.startswith(_POSTGRESQL_TEXT_TYPES)
    if value_type is int:
        return True
    bits = _POSTGRESQL_INTEGER_BITS.get(value_type)
    if bits is None:
        return False
    # One value past the size would fail the whole array.
    bound = 1 << (bits - 1)
    return -bound <= min(values) and max(values) < bound


def bind_long_lists():
    In.as_sqlite = as_sqlite
    In.as_postgresql = as_postgresql
