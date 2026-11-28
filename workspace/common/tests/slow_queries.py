"""Queries that keep the database busy, on either backend."""

from django.db import connection

_COUNT_TO = (
    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) "
    "SELECT count(*) FROM (SELECT x FROM n LIMIT %s) AS counted"
)


def count_to(limit):
    """Counts one row at a time: a long run of database work, all of it in
    one statement, with no table to read."""
    with connection.cursor() as cursor:
        cursor.execute(_COUNT_TO, [limit])
        return cursor.fetchone()[0]


def run_endless_query():
    """Runs until something interrupts it."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(60)")
            return cursor.fetchone()
    return count_to(10**12)
