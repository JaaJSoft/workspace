import time

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.test import TestCase, TransactionTestCase

from workspace.common.query_deadline import QueryDeadlineExceeded, query_deadline

from .slow_queries import count_to, run_endless_query

User = get_user_model()

BUDGET = 0.2
# Far past the budget, far short of the query: a slack for a slow CI runner.
ON_TIME = 3.0


def _statement_timeout():
    with connection.cursor() as cursor:
        cursor.execute("SHOW statement_timeout")
        return cursor.fetchone()[0]


class QueryDeadlineTests(TestCase):
    def test_a_query_past_the_budget_is_interrupted(self):
        started = time.monotonic()

        with self.assertRaises(QueryDeadlineExceeded), query_deadline(BUDGET):
            run_endless_query()

        self.assertLess(time.monotonic() - started, ON_TIME)

    def test_queries_within_the_budget_answer(self):
        User.objects.create_user(username="alice", password="x")

        with query_deadline(BUDGET):
            count = User.objects.count()

        self.assertEqual(count, 1)

    def test_a_query_started_after_the_budget_is_stopped(self):
        with self.assertRaises(QueryDeadlineExceeded), query_deadline(0.05):
            time.sleep(0.1)
            count_to(100_000)

    def test_the_transaction_survives_an_interrupted_query(self):
        with self.assertRaises(QueryDeadlineExceeded), query_deadline(BUDGET):
            run_endless_query()

        self.assertEqual(User.objects.count(), 0)

    def test_queries_after_the_block_are_no_longer_bounded(self):
        with self.assertRaises(QueryDeadlineExceeded), query_deadline(0.05):
            time.sleep(0.1)
            count_to(100_000)

        self.assertEqual(count_to(100_000), 100_000)

    def test_rows_written_within_the_budget_are_kept(self):
        with query_deadline(BUDGET):
            User.objects.create_user(username="alice", password="x")

        self.assertTrue(User.objects.filter(username="alice").exists())

    def test_a_database_error_within_the_budget_is_left_as_it_is(self):
        with self.assertRaises(DatabaseError) as caught, query_deadline(BUDGET):
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM no_such_table")

        self.assertNotIsInstance(caught.exception, QueryDeadlineExceeded)

    def test_other_errors_pass_through(self):
        with self.assertRaises(KeyError), query_deadline(BUDGET):
            raise KeyError("boom")

    def test_the_statement_timeout_is_put_back(self):
        if connection.vendor != "postgresql":
            self.skipTest("statement_timeout is PostgreSQL's")
        before = _statement_timeout()

        with query_deadline(BUDGET):
            User.objects.count()
        with self.assertRaises(QueryDeadlineExceeded), query_deadline(BUDGET):
            run_endless_query()

        self.assertEqual(_statement_timeout(), before)


class QueryDeadlineOutsideATransactionTests(TransactionTestCase):
    def test_a_query_past_the_budget_is_interrupted(self):
        started = time.monotonic()

        with self.assertRaises(QueryDeadlineExceeded), query_deadline(BUDGET):
            run_endless_query()

        self.assertLess(time.monotonic() - started, ON_TIME)
        self.assertEqual(User.objects.count(), 0)

    def test_sqlite_opens_no_transaction(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite reads outside a transaction")

        with query_deadline(BUDGET):
            self.assertFalse(connection.in_atomic_block)

    def test_the_statement_timeout_ends_with_the_block(self):
        if connection.vendor != "postgresql":
            self.skipTest("statement_timeout is PostgreSQL's")
        before = _statement_timeout()

        with query_deadline(BUDGET):
            User.objects.count()

        self.assertEqual(_statement_timeout(), before)
