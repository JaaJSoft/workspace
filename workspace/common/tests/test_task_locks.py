"""Tests for the advisory task lock used to stop beat runs from stacking."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from workspace.common.task_locks import task_lock


class TaskLockTests(TestCase):
    def tearDown(self):
        # LocMemCache is process-global and is not reset between TestCase
        # runs, so a leaked key would make unrelated tests skip their work.
        cache.clear()

    def test_acquires_a_free_lock(self):
        with task_lock("job:free", 60) as acquired:
            self.assertTrue(acquired)

    def test_second_holder_is_refused_while_the_first_holds_it(self):
        with task_lock("job:contended", 60) as first:
            self.assertTrue(first)
            with task_lock("job:contended", 60) as second:
                self.assertFalse(second)

    def test_lock_is_released_on_exit(self):
        with task_lock("job:sequential", 60) as first:
            self.assertTrue(first)

        with task_lock("job:sequential", 60) as second:
            self.assertTrue(second)

    def test_lock_is_released_when_the_body_raises(self):
        # A crashed run must not block the next scheduled attempt for the
        # whole TTL.
        with self.assertRaises(ValueError):
            with task_lock("job:boom", 60) as acquired:
                self.assertTrue(acquired)
                raise ValueError("boom")

        self.assertIsNone(cache.get("job:boom"))

    def test_a_refused_holder_does_not_release_the_owners_lock(self):
        # The loser of the race must not delete the winner's key on its way
        # out, which would hand the lock to a third caller mid-run.
        cache.add("job:owned", "locked", 60)

        with task_lock("job:owned", 60) as acquired:
            self.assertFalse(acquired)

        self.assertEqual(cache.get("job:owned"), "locked")

    def test_distinct_keys_do_not_contend(self):
        with task_lock("job:a", 60) as a:
            with task_lock("job:b", 60) as b:
                self.assertTrue(a)
                self.assertTrue(b)

    def test_ttl_is_passed_through_to_the_cache(self):
        with mock.patch.object(cache, "add", return_value=True) as add:
            with task_lock("job:ttl", 1800):
                pass

        self.assertEqual(add.call_args.args[0], "job:ttl")
        self.assertEqual(add.call_args.args[2], 1800)

    def test_release_failure_is_swallowed(self):
        # A cache blip on release only means the lock lingers until its TTL,
        # which must not surface as a task failure.
        with mock.patch.object(cache, "delete", side_effect=RuntimeError("gone")):
            with task_lock("job:flaky-release", 60) as acquired:
                self.assertTrue(acquired)
