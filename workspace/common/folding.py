"""Accent- and case-insensitive substring matching in the database.

``Fold(expr)`` is :func:`workspace.common.text.fold_text` run by the database,
and ``field__ficontains=needle`` is ``icontains`` on both sides folded:
``from_name__ficontains="francois"`` finds "François".

PostgreSQL folds with ``f_unaccent(lower(...))`` (``common/0001_f_unaccent``),
SQLite with ``fold_text`` itself, registered on every connection. The needle
is folded by the same SQL as the column, so the two always agree even where
PostgreSQL's ``unaccent`` and ``fold_text`` differ (``"œ"`` gives ``"oe"`` in
one, stays ``"œ"`` in the other). No index serves either form: it is a scan,
like the ``icontains`` it replaces.
"""

from django.db import NotSupportedError
from django.db.models import CharField, Func, TextField, Value
from django.db.models.lookups import Contains

from workspace.common.text import fold_text

SQLITE_FUNCTION = "fold_text"


class Fold(Func):
    output_field = TextField()
    arity = 1

    def as_sql(self, compiler, connection, **extra_context):
        raise NotSupportedError(f"Fold is not implemented on {connection.vendor}")

    def as_sqlite(self, compiler, connection, **extra_context):
        return super().as_sql(
            compiler, connection, template=f"{SQLITE_FUNCTION}(%(expressions)s)"
        )

    def as_postgresql(self, compiler, connection, **extra_context):
        return super().as_sql(
            compiler, connection, template="f_unaccent(lower(%(expressions)s))"
        )


@CharField.register_lookup
@TextField.register_lookup
class FoldedContains(Contains):
    lookup_name = "ficontains"

    def get_prep_lookup(self):
        rhs = self.rhs
        if not hasattr(rhs, "resolve_expression"):
            rhs = Value(str(rhs), output_field=TextField())
        return Fold(rhs)

    def get_prep_lhs(self):
        return Fold(super().get_prep_lhs())

    def get_rhs_op(self, connection, rhs):
        # The folded needle is an expression, so the LIKE pattern is built in
        # SQL, with its % and _ escaped. pattern_ops only knows the built-in
        # lookup names.
        pattern = connection.pattern_ops["contains"].format(connection.pattern_esc)
        return pattern.format(rhs)


def _sqlite_fold(value):
    return None if value is None else fold_text(str(value))


def register_sqlite_fold(sender, connection, **kwargs):
    """connection_created receiver: every new SQLite connection gets ``fold_text``."""
    if connection.vendor != "sqlite":
        return
    connection.connection.create_function(
        SQLITE_FUNCTION, 1, _sqlite_fold, deterministic=True
    )
