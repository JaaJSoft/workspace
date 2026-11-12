"""Brings SQLite's per-statement limits within reach of a test."""

import sqlite3
from contextlib import contextmanager

from django.db import connection


@contextmanager
def sqlite_parameter_limit(limit):
    """Run the block with at most *limit* bound parameters per statement.

    SQLite refuses a statement past 32766 parameters, and some builds raise
    that ceiling, so a test cannot count on reaching it. Lowered to a few
    dozen, a handful of rows is enough to prove a query does not grow with its
    input. Wrap the call under test only: an ordinary INSERT of a wide model
    binds more than that.

    Other databases have no such limit to lower; the block runs unchanged.
    """
    if connection.vendor != "sqlite":
        yield
        return
    connection.ensure_connection()
    raw = connection.connection
    previous = raw.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, limit)
    try:
        yield
    finally:
        raw.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous)


@contextmanager
def sqlite_expression_depth(limit):
    """Run the block with expressions at most *limit* levels deep.

    SQLite refuses an expression nested past a thousand levels, which a chain
    of ``OR`` reaches with as many terms. Lowered, a few dozen rows prove a
    query does not write one condition per row.

    Other databases have no such limit to lower; the block runs unchanged.
    """
    if connection.vendor != "sqlite":
        yield
        return
    connection.ensure_connection()
    raw = connection.connection
    previous = raw.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, limit)
    try:
        yield
    finally:
        raw.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, previous)
