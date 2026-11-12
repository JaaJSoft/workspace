"""``field__in_array=values``: ``__in`` for a list of any size.

``__in`` binds one parameter per value, and SQLite refuses a statement
carrying more than 32766 of them. ``in_array`` hands the whole list to the
database as one parameter, so the size of the list no longer shows in the
statement::

    MailMessage.objects.filter(imap_uid__in_array=uids).update(is_read=True)

SQLite reads the values back out of a JSON array (``json_each``), PostgreSQL
compares against an array (``= ANY``). Either way it stays one queryset, which
a list cut into batches does not: it can be ordered, counted, updated or
deleted as a whole.

It takes values, never model instances, and never a queryset: a subquery binds
nothing per row, so ``__in`` is already right for it.
"""

import json

from django.core.exceptions import EmptyResultSet
from django.db import NotSupportedError
from django.db.models import Field, ForeignObject
from django.db.models.lookups import Lookup

# What JSON carries to SQLite unchanged. A float is parsed again by SQLite's
# JSON reader, with no guarantee it lands on the exact value Python stored.
_SQLITE_JSON_TYPES = (str, int)


# Related fields do not inherit the lookups registered on Field.
@Field.register_lookup
@ForeignObject.register_lookup
class InArray(Lookup):
    lookup_name = "in_array"

    def get_prep_lookup(self):
        values = self.rhs
        if isinstance(values, (str, bytes)) or hasattr(values, "resolve_expression"):
            raise TypeError("in_array takes a list of values; use __in for a subquery")
        prepare = self.lhs.output_field.get_prep_value
        # Like __in: NULL matches nothing, and a repeated value adds nothing.
        return list(dict.fromkeys(prepare(v) for v in values if v is not None))

    def _db_values(self, connection):
        if not self.rhs:
            raise EmptyResultSet
        prepare = self.lhs.output_field.get_db_prep_value
        return [prepare(value, connection, prepared=True) for value in self.rhs]

    def as_sql(self, compiler, connection):
        raise NotSupportedError(f"in_array is not implemented on {connection.vendor}")

    def as_sqlite(self, compiler, connection):
        values = self._db_values(connection)
        unsupported = {type(v) for v in values} - set(_SQLITE_JSON_TYPES)
        if unsupported:
            names = ", ".join(sorted(t.__name__ for t in unsupported))
            raise NotSupportedError(
                f"in_array on SQLite takes text and integer values, got {names}"
            )
        lhs, lhs_params = self.process_lhs(compiler, connection)
        return (
            f"{lhs} IN (SELECT value FROM json_each(%s))",
            (*lhs_params, json.dumps(values, ensure_ascii=False)),
        )

    def as_postgresql(self, compiler, connection):
        values = self._db_values(connection)
        lhs, lhs_params = self.process_lhs(compiler, connection)
        return f"{lhs} = ANY(%s)", (*lhs_params, values)
