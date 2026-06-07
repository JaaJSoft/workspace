from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.users.models import UserSetting
from workspace.users.services.settings import (
    delete_setting,
    get_all_settings,
    get_module_settings,
    get_setting,
    get_user_timezone,
    set_setting,
)

User = get_user_model()


class GetUserTimezoneTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="alice", password="pass")

    def test_returns_utc_when_no_setting(self):
        tz = get_user_timezone(self.user)
        self.assertEqual(str(tz), "UTC")

    def test_returns_configured_timezone(self):
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        tz = get_user_timezone(self.user)
        self.assertEqual(str(tz), "Europe/Paris")

    def test_returns_utc_for_invalid_timezone(self):
        set_setting(self.user, "core", "timezone", "Not/ATimezone")
        tz = get_user_timezone(self.user)
        self.assertEqual(str(tz), "UTC")

    def test_returns_utc_for_empty_string(self):
        set_setting(self.user, "core", "timezone", "")
        tz = get_user_timezone(self.user)
        self.assertEqual(str(tz), "UTC")


class GetSettingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="alice", password="pass")

    def test_returns_default_when_not_found(self):
        self.assertIsNone(get_setting(self.user, "core", "missing"))
        self.assertEqual(get_setting(self.user, "core", "missing", default=42), 42)

    def test_returns_value(self):
        set_setting(self.user, "core", "theme", "dark")
        self.assertEqual(get_setting(self.user, "core", "theme"), "dark")

    def test_returns_json_value(self):
        set_setting(self.user, "core", "prefs", {"a": 1})
        self.assertEqual(get_setting(self.user, "core", "prefs"), {"a": 1})

    def test_returns_null_value(self):
        set_setting(self.user, "core", "key", None)
        # Explicitly check it returns None, not the default
        result = get_setting(self.user, "core", "key", default="fallback")
        self.assertIsNone(result)


class SetSettingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="alice", password="pass")

    def test_creates_new_setting(self):
        obj = set_setting(self.user, "core", "theme", "dark")
        self.assertEqual(obj.value, "dark")
        self.assertEqual(UserSetting.objects.count(), 1)

    def test_updates_existing_setting(self):
        set_setting(self.user, "core", "theme", "light")
        set_setting(self.user, "core", "theme", "dark")
        self.assertEqual(UserSetting.objects.count(), 1)
        self.assertEqual(
            UserSetting.objects.get(user=self.user, module="core", key="theme").value,
            "dark",
        )

    def test_returns_model_instance(self):
        obj = set_setting(self.user, "core", "theme", "dark")
        self.assertIsInstance(obj, UserSetting)

    def test_skips_update_or_create_when_value_unchanged(self):
        # The point of the optimization is that no_op calls do NOT enter
        # the update_or_create path - which is the path that opens a
        # transaction and (on SQLite) takes the writer-lock. Spying on
        # that method is more meaningful than counting queries because
        # the no-op fast path still does ONE cheap SELECT to honour the
        # return-type contract - the win is "no UPDATE", not "no query".
        from unittest.mock import patch

        set_setting(self.user, "core", "theme", "dark")
        # Warm the read cache (set_setting invalidates it on write).
        get_setting(self.user, "core", "theme")

        original = UserSetting.objects.update_or_create
        with patch.object(
            UserSetting.objects,
            "update_or_create",
            wraps=original,
        ) as spy:
            set_setting(self.user, "core", "theme", "dark")
        spy.assert_not_called()

    def test_does_not_take_writer_path_when_cached_value_matches(self):
        # Sanity-check on query count too: with a warm cache, the no-op
        # path costs exactly one indexed SELECT (the .get() that returns
        # the model instance) and zero writes.
        set_setting(self.user, "core", "theme", "dark")
        get_setting(self.user, "core", "theme")  # warm the read cache

        with self.assertNumQueries(1):
            set_setting(self.user, "core", "theme", "dark")

    def test_skips_update_or_create_when_cache_cold_but_db_already_matches(self):
        # First-write-after-deploy scenario: the Redis cache is cold but
        # the SQLite row already has the target value (e.g. user clicks
        # their currently-active theme on a freshly-started worker).
        # The fast path must still kick in based on the *DB* value
        # populated into the cache by _get_setting_raw, not require a
        # pre-warmed cache.
        from unittest.mock import patch

        # Plant the row directly so the read-side cache stays cold.
        UserSetting.objects.create(
            user=self.user,
            module="core",
            key="theme",
            value="dark",
        )
        cache.clear()

        original = UserSetting.objects.update_or_create
        with patch.object(
            UserSetting.objects,
            "update_or_create",
            wraps=original,
        ) as spy:
            set_setting(self.user, "core", "theme", "dark")
        spy.assert_not_called()

    def test_writes_when_value_changes(self):
        set_setting(self.user, "core", "theme", "light")
        set_setting(self.user, "core", "theme", "dark")
        self.assertEqual(
            UserSetting.objects.get(
                user=self.user,
                module="core",
                key="theme",
            ).value,
            "dark",
        )

    def test_noop_does_not_invalidate_cache(self):
        # If a no-op call were to invalidate the cache anyway, every
        # redundant click would still cost a Redis round-trip (and the
        # next read would refill the cache from the DB). The whole point
        # of the optimization is to be free on no-op.
        set_setting(self.user, "core", "theme", "dark")
        # Warm the read-side cache.
        self.assertEqual(get_setting(self.user, "core", "theme"), "dark")

        set_setting(self.user, "core", "theme", "dark")  # no-op

        # The cache must still be hot - this get must hit zero queries.
        with self.assertNumQueries(0):
            self.assertEqual(get_setting(self.user, "core", "theme"), "dark")

    def test_falls_through_when_cache_lies_about_existence(self):
        # Edge case: the cache says the row exists with the target value,
        # but the row was deleted out-of-band (a direct ORM delete that
        # bypassed delete_setting). The no-op fast path must catch the
        # DoesNotExist and fall through to the create branch.
        set_setting(self.user, "core", "theme", "dark")
        # Warm the cache with the value.
        get_setting(self.user, "core", "theme")

        # Delete the row WITHOUT going through delete_setting so the
        # cache is now stale.
        UserSetting.objects.filter(
            user=self.user,
            module="core",
            key="theme",
        ).delete()

        # Should re-create, not raise.
        obj = set_setting(self.user, "core", "theme", "dark")
        self.assertEqual(obj.value, "dark")
        self.assertTrue(
            UserSetting.objects.filter(
                user=self.user,
                module="core",
                key="theme",
            ).exists()
        )

    def test_falls_through_when_cache_says_match_but_db_has_different_value(self):
        # Race scenario: a concurrent writer mutated the DB after our
        # process's cache was warmed. Our cache still shows the old
        # value (which happens to match what the caller wants to set),
        # but the DB drifted to a different value.
        #
        # Without re-verifying the .get() result against the target
        # value, the no-op fast path would short-circuit on the stale
        # cache and silently drop the caller's write, leaving the DB at
        # the concurrent writer's value.
        set_setting(self.user, "core", "theme", "dark")
        get_setting(self.user, "core", "theme")  # warm cache with 'dark'

        # Mutate the DB without going through set_setting so our cache
        # stays unchanged (simulates a different process whose
        # invalidate_tags broadcast hasn't reached us, or was lost).
        UserSetting.objects.filter(
            user=self.user,
            module="core",
            key="theme",
        ).update(value="light")

        # Cache: 'dark'. DB: 'light'. Caller wants 'dark'.
        # The fast path matches on cache but must NOT skip the write -
        # the freshly-fetched row carries the drifted DB value and the
        # mismatch must be detected.
        set_setting(self.user, "core", "theme", "dark")

        self.assertEqual(
            UserSetting.objects.get(
                user=self.user,
                module="core",
                key="theme",
            ).value,
            "dark",
        )

    def tearDown(self):
        # LocMemCache is process-global; without tearDown the cache
        # entries written by set_setting / get_setting leak into the
        # next test class and cause order-dependent failures.
        cache.clear()


class DeleteSettingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="alice", password="pass")

    def test_returns_true_when_deleted(self):
        set_setting(self.user, "core", "theme", "dark")
        self.assertTrue(delete_setting(self.user, "core", "theme"))
        self.assertEqual(UserSetting.objects.count(), 0)

    def test_returns_false_when_not_found(self):
        self.assertFalse(delete_setting(self.user, "core", "missing"))


class GetModuleSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="alice", password="pass")

    def test_returns_empty_dict_when_none(self):
        self.assertEqual(get_module_settings(self.user, "core"), {})

    def test_returns_settings_for_module(self):
        set_setting(self.user, "core", "theme", "dark")
        set_setting(self.user, "core", "lang", "fr")
        set_setting(self.user, "mail", "sig", "bye")  # different module
        result = get_module_settings(self.user, "core")
        self.assertEqual(result, {"theme": "dark", "lang": "fr"})


class GetAllSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="alice", password="pass")

    def test_returns_empty_list_when_none(self):
        self.assertEqual(get_all_settings(self.user), [])

    def test_returns_all_settings(self):
        set_setting(self.user, "core", "theme", "dark")
        set_setting(self.user, "mail", "sig", "bye")
        result = get_all_settings(self.user)
        self.assertEqual(len(result), 2)
        modules = {r["module"] for r in result}
        self.assertEqual(modules, {"core", "mail"})


class SettingCacheTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_get_setting_caches_value(self):
        set_setting(self.user, "core", "theme", "dark")
        # First call populates cache
        self.assertEqual(get_setting(self.user, "core", "theme"), "dark")
        # Second call should not hit DB
        with self.assertNumQueries(0):
            self.assertEqual(get_setting(self.user, "core", "theme"), "dark")

    def test_get_setting_caches_default(self):
        # First call — cache miss, DB miss, caches default
        self.assertIsNone(get_setting(self.user, "core", "missing"))
        # Second call — served from cache, no DB
        with self.assertNumQueries(0):
            self.assertIsNone(get_setting(self.user, "core", "missing"))

    def test_get_setting_caches_none_value(self):
        set_setting(self.user, "core", "key", None)
        cache.clear()
        # First call caches the real None value
        result = get_setting(self.user, "core", "key", default="fallback")
        self.assertIsNone(result)
        # Second call returns cached None, not the default
        with self.assertNumQueries(0):
            result = get_setting(self.user, "core", "key", default="fallback")
            self.assertIsNone(result)

    def test_set_setting_invalidates_cache(self):
        set_setting(self.user, "core", "theme", "light")
        # Warm cache with the old value
        self.assertEqual(get_setting(self.user, "core", "theme"), "light")
        set_setting(self.user, "core", "theme", "dark")
        # Next read reflects the new value (cache was invalidated)
        self.assertEqual(get_setting(self.user, "core", "theme"), "dark")
        # And is now cached — second read is a hit
        with self.assertNumQueries(0):
            self.assertEqual(get_setting(self.user, "core", "theme"), "dark")

    def test_delete_setting_invalidates_cache(self):
        set_setting(self.user, "core", "theme", "dark")
        # Warm cache
        get_setting(self.user, "core", "theme")
        delete_setting(self.user, "core", "theme")
        # Cache should be cleared — next get hits DB and returns default
        self.assertIsNone(get_setting(self.user, "core", "theme"))

    def test_get_module_settings_caches_result(self):
        set_setting(self.user, "profile", "bio", "hi")
        set_setting(self.user, "profile", "role", "dev")
        # First call warms the module cache
        self.assertEqual(
            get_module_settings(self.user, "profile"),
            {"bio": "hi", "role": "dev"},
        )
        # Second call served from cache — no DB hit
        with self.assertNumQueries(0):
            self.assertEqual(
                get_module_settings(self.user, "profile"),
                {"bio": "hi", "role": "dev"},
            )

    def test_set_setting_invalidates_module_cache(self):
        set_setting(self.user, "profile", "bio", "old")
        # Warm module cache
        get_module_settings(self.user, "profile")
        set_setting(self.user, "profile", "bio", "new")
        # Next read must reflect the new value, not the cached old one
        self.assertEqual(
            get_module_settings(self.user, "profile"),
            {"bio": "new"},
        )

    def test_delete_setting_invalidates_module_cache(self):
        set_setting(self.user, "profile", "bio", "hi")
        # Warm module cache
        get_module_settings(self.user, "profile")
        delete_setting(self.user, "profile", "bio")
        # Deleted key must disappear from the module dict
        self.assertEqual(get_module_settings(self.user, "profile"), {})
