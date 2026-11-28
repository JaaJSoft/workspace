"""Stop the queries of a block of code once a time budget is spent.

::

    with query_deadline(1.0):
        hits = provider(query)      # QueryDeadlineExceeded past one second

A query running when the time runs out is interrupted in the database, not
waited for: a progress handler on SQLite, ``statement_timeout`` on
PostgreSQL. Work outside the database is never interrupted, so a block that
spends its time in Python finishes late rather than raising.

The block runs in a savepoint when a transaction is already open, so an
interrupted query does not leave that transaction aborted. Otherwise
PostgreSQL gets a transaction of its own, which ``SET LOCAL`` needs, and
SQLite none: under ``transaction_mode="IMMEDIATE"`` a read would take the
writer lock.

A query whose time runs out raises ``QueryDeadlineExceeded``, a
``DatabaseError``. A statement short enough to do no real work may still
complete past the deadline on SQLite: its handler is only called every
``SQLITE_PROGRESS_STEPS`` instructions.
"""

import math
import time
from contextlib import contextmanager, nullcontext

from django.db import DEFAULT_DB_ALIAS, DatabaseError, connections, transaction

# VM instructions between two looks at the clock: microseconds of work.
SQLITE_PROGRESS_STEPS = 1000


class QueryDeadlineExceeded(DatabaseError):
    """The block was still querying the database when its budget ran out."""


@contextmanager
def query_deadline(seconds, using=DEFAULT_DB_ALIAS):
    connection = connections[using]
    deadline = time.monotonic() + seconds
    in_transaction = connection.in_atomic_block

    def expired():
        return time.monotonic() >= deadline

    try:
        with (
            _transaction_for(connection, in_transaction),
            _interrupt(connection, deadline, expired, in_transaction),
        ):
            yield
    except QueryDeadlineExceeded:
        raise
    except DatabaseError as exc:
        if expired():
            raise QueryDeadlineExceeded(
                f"queries ran past their {seconds:g}s budget"
            ) from exc
        raise


def _transaction_for(connection, in_transaction):
    # SET LOCAL needs a transaction on PostgreSQL, and its effect ends with it.
    if in_transaction or connection.vendor == "postgresql":
        return transaction.atomic(using=connection.alias)
    return nullcontext()


def _interrupt(connection, deadline, expired, in_transaction):
    if connection.vendor == "sqlite":
        return _sqlite_interrupt(connection, expired)
    if connection.vendor == "postgresql":
        return _postgresql_interrupt(connection, deadline, in_transaction)
    return _refuse_late_queries(connection, expired)


@contextmanager
def _sqlite_interrupt(connection, expired):
    # For the whole block, not per statement: Django reads the rows of a
    # query after cursor.execute returns, and on SQLite that is where most
    # of a scan's work happens. A connection holds a single handler, so
    # deadlines do not nest.
    connection.ensure_connection()
    raw = connection.connection
    raw.set_progress_handler(expired, SQLITE_PROGRESS_STEPS)
    try:
        yield
    finally:
        raw.set_progress_handler(None, SQLITE_PROGRESS_STEPS)


@contextmanager
def _postgresql_interrupt(connection, deadline, in_transaction):
    previous = None
    if in_transaction:
        # A released savepoint keeps its SET LOCAL until the outer
        # transaction ends, so the outer value is put back by hand.
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('statement_timeout')")
            previous = cursor.fetchone()[0]

    def bounded(execute, sql, params, many, context):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise QueryDeadlineExceeded("no budget left for another query")
        # 0 would disable the timeout; a statement gets what is left of the
        # budget, so a block of several cannot run past its end.
        milliseconds = max(1, math.ceil(remaining * 1000))
        execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (f"{milliseconds}ms",),
            False,
            context,
        )
        return execute(sql, params, many, context)

    with connection.execute_wrapper(bounded):
        yield
    if in_transaction:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)", (previous,)
            )


@contextmanager
def _refuse_late_queries(connection, expired):
    def refuse(execute, sql, params, many, context):
        if expired():
            raise QueryDeadlineExceeded("no budget left for another query")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(refuse):
        yield
