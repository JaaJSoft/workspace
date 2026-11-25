from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import translation
from rest_framework import serializers

from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.users.models import UserSetting
from workspace.users.services.settings import (
    delete_setting,
    get_module_settings,
    get_setting,
    get_user_language,
    get_user_languages,
    get_user_time_formats,
    get_user_timezone,
    in_recipient_languages,
    set_setting,
    write_settings,
)
from workspace.users.settings_registry import UnknownModule
from workspace.users.tests.settings_api import SettingsApiTestCase

User = get_user_model()


class GetUserTimezoneTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="alice", password="pass")

    def tearDown(self):
        cache.clear()

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

    def tearDown(self):
        cache.clear()

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
        # populated into the cache by get_module_settings, not require a
        # pre-warmed cache.

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

    def tearDown(self):
        cache.clear()

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

    def tearDown(self):
        cache.clear()

    def test_returns_empty_dict_when_none(self):
        self.assertEqual(get_module_settings(self.user, "core"), {})

    def test_returns_settings_for_module(self):
        set_setting(self.user, "core", "theme", "dark")
        set_setting(self.user, "core", "lang", "fr")
        set_setting(self.user, "mail", "sig", "bye")  # different module
        result = get_module_settings(self.user, "core")
        self.assertEqual(result, {"theme": "dark", "lang": "fr"})


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
        # First call - cache miss, DB miss, caches default
        self.assertIsNone(get_setting(self.user, "core", "missing"))
        # Second call - served from cache, no DB
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

    def test_reading_one_key_warms_the_whole_module(self):
        # get_setting reads through get_module_settings, so the first read of
        # any key warms every other key in that module - a different present
        # key AND an absent key are both served without touching the DB.
        set_setting(self.user, "core", "theme", "dark")
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        cache.clear()

        # Cold read of one key issues a single query for the whole module.
        with self.assertNumQueries(1):
            self.assertEqual(get_setting(self.user, "core", "theme"), "dark")

        # Every other read in the same module is now free.
        with self.assertNumQueries(0):
            self.assertEqual(get_setting(self.user, "core", "timezone"), "Europe/Paris")
            self.assertEqual(
                get_setting(self.user, "core", "missing", default="x"), "x"
            )

    def test_set_setting_invalidates_cache(self):
        set_setting(self.user, "core", "theme", "light")
        # Warm cache with the old value
        self.assertEqual(get_setting(self.user, "core", "theme"), "light")
        set_setting(self.user, "core", "theme", "dark")
        # Next read reflects the new value (cache was invalidated)
        self.assertEqual(get_setting(self.user, "core", "theme"), "dark")
        # And is now cached - second read is a hit
        with self.assertNumQueries(0):
            self.assertEqual(get_setting(self.user, "core", "theme"), "dark")

    def test_delete_setting_invalidates_cache(self):
        set_setting(self.user, "core", "theme", "dark")
        # Warm cache
        get_setting(self.user, "core", "theme")
        delete_setting(self.user, "core", "theme")
        # Cache should be cleared - next get hits DB and returns default
        self.assertIsNone(get_setting(self.user, "core", "theme"))

    def test_get_module_settings_caches_result(self):
        set_setting(self.user, "profile", "bio", "hi")
        set_setting(self.user, "profile", "role", "dev")
        # First call warms the module cache
        self.assertEqual(
            get_module_settings(self.user, "profile"),
            {"bio": "hi", "role": "dev"},
        )
        # Second call served from cache - no DB hit
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


class GetUserTimezonePathValueTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="tzpath", password="pass")

    def tearDown(self):
        cache.clear()

    def test_returns_utc_for_path_like_value(self):
        # ZoneInfo raises ValueError (not ZoneInfoNotFoundError) for path-like
        # keys; a value planted via shell/fixture must not 500 every request.
        set_setting(self.user, "core", "timezone", "../etc")
        self.assertEqual(str(get_user_timezone(self.user)), "UTC")


class LanguageSettingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="langval", password="pass")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_user_language_defaults_to_language_code(self):
        self.assertEqual(get_user_language(self.user), "en")

    def test_user_language_returns_stored_choice(self):
        set_setting(self.user, "core", "language", "fr")
        self.assertEqual(get_user_language(self.user), "fr")

    @override_settings(LANGUAGES=[("en", "English")])
    def test_user_language_drops_a_language_no_longer_offered(self):
        set_setting(self.user, "core", "language", "fr")
        self.assertEqual(get_user_language(self.user), "en")


class GetUserLanguagesTests(TestCase):
    def setUp(self):
        self.users = [
            User.objects.create_user(username=f"lang{i}", password="pass")
            for i in range(3)
        ]

    def tearDown(self):
        cache.clear()

    def test_reads_every_language_in_one_query(self):
        set_setting(self.users[0], "core", "language", "fr")
        set_setting(self.users[1], "core", "language", "xx")
        with self.assertNumQueries(1):
            languages = get_user_languages([u.pk for u in self.users])
        self.assertEqual(
            languages,
            {self.users[0].pk: "fr", self.users[1].pk: "en", self.users[2].pk: "en"},
        )

    def test_no_user_costs_no_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(get_user_languages([]), {})

    def test_a_fan_out_larger_than_a_statement_binds_stays_one_query(self):
        # SQLite caps the parameters of one query (32766, lowered here): a
        # fan-out to every active user must not bind one per user.
        set_setting(self.users[0], "core", "language", "fr")
        set_setting(self.users[2], "core", "language", "fr")
        limit = 20
        user_ids = [u.pk for u in self.users]
        strangers = [max(user_ids) + offset for offset in range(1, limit * 2)]
        with sqlite_parameter_limit(limit), self.assertNumQueries(1):
            languages = get_user_languages(user_ids + strangers)
        self.assertEqual(
            {user_id: languages[user_id] for user_id in user_ids},
            {self.users[0].pk: "fr", self.users[1].pk: "en", self.users[2].pk: "fr"},
        )


class GetUserTimeFormatsTests(TestCase):
    def tearDown(self):
        cache.clear()

    def test_reads_every_clock_in_one_query_24h_by_default(self):
        users = [
            User.objects.create_user(username=f"clock{i}", password="pass")
            for i in range(3)
        ]
        set_setting(users[0], "core", "time_format", "12h")
        # A value written before the choices were enforced.
        UserSetting.objects.create(
            user=users[1], module="core", key="time_format", value="36h"
        )
        with self.assertNumQueries(1):
            formats = get_user_time_formats([u.pk for u in users])
        self.assertEqual(
            formats, {users[0].pk: "12h", users[1].pk: "24h", users[2].pk: "24h"}
        )


class InRecipientLanguagesTests(TestCase):
    def setUp(self):
        self.camille = User.objects.create_user(username="camille", password="pass")
        self.dominique = User.objects.create_user(username="dominique", password="pass")
        self.alex = User.objects.create_user(username="alex", password="pass")
        set_setting(self.camille, "core", "language", "fr")
        set_setting(self.dominique, "core", "language", "fr")

    def tearDown(self):
        cache.clear()

    def test_groups_recipients_by_language_with_it_active(self):
        groups = {}
        for users in in_recipient_languages([self.camille, self.alex, self.dominique]):
            groups[translation.get_language()] = {u.username for u in users}
        self.assertEqual(groups, {"fr": {"camille", "dominique"}, "en": {"alex"}})

    def test_restores_the_language_afterwards(self):
        with translation.override("en"):
            for _users in in_recipient_languages([self.camille]):
                pass
            self.assertEqual(translation.get_language(), "en")


class WriteSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="writer", password="pass")

    def tearDown(self):
        cache.clear()

    def test_stores_what_the_fields_made_of_the_values(self):
        stored = write_settings(self.user, "profile", {"bio": "  hello  ", "role": 7})
        self.assertEqual(stored, {"bio": "hello", "role": "7"})
        self.assertEqual(get_module_settings(self.user, "profile"), stored)

    def test_a_null_removes_its_key(self):
        set_setting(self.user, "profile", "bio", "hello")
        stored = write_settings(self.user, "profile", {"bio": None, "role": "dev"})
        self.assertEqual(stored, {"bio": None, "role": "dev"})
        self.assertEqual(get_module_settings(self.user, "profile"), {"role": "dev"})

    def test_one_refused_value_writes_nothing(self):
        with self.assertRaises(serializers.ValidationError):
            write_settings(self.user, "profile", {"role": "dev", "bio": "x" * 201})
        self.assertEqual(get_module_settings(self.user, "profile"), {})

    def test_a_write_that_fails_midway_stores_nothing(self):
        store = set_setting
        written = []

        def fail_on_the_second(user, module, key, value):
            written.append(key)
            if len(written) == 2:
                raise RuntimeError("storage failed")
            return store(user, module, key, value)

        with (
            patch(
                "workspace.users.services.settings.set_setting",
                side_effect=fail_on_the_second,
            ),
            self.assertRaises(RuntimeError),
        ):
            write_settings(self.user, "profile", {"bio": "hello", "role": "dev"})
        self.assertEqual(len(written), 2)
        self.assertFalse(UserSetting.objects.filter(user=self.user).exists())

    def test_a_key_only_the_server_writes_is_refused(self):
        with self.assertRaises(serializers.ValidationError):
            write_settings(self.user, "profile", {"has_avatar": True})
        self.assertEqual(get_module_settings(self.user, "profile"), {})

    def test_an_unknown_module_is_refused(self):
        with self.assertRaises(UnknownModule):
            write_settings(self.user, "attic", {"bio": "hello"})


class SettingsApiTests(SettingsApiTestCase):
    """What the API does whatever the module; each module tests its own
    settings in its ``tests/test_user_settings.py``."""

    module = "profile"

    def test_a_setting_no_module_declares_is_not_found(self):
        for module, key in (("attic", "bio"), ("profile", "shoe_size")):
            with self.subTest(module=module, key=key):
                self.assertEqual(self.put(key, "x", module).status_code, 404)
                url = f"/api/v1/settings/{module}/{key}"
                self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertFalse(UserSetting.objects.filter(user=self.user).exists())

    def test_a_setting_only_the_server_writes_is_not_found(self):
        set_setting(self.user, "profile", "has_avatar", True)
        self.assertEqual(self.put("has_avatar", False).status_code, 404)
        response = self.client.delete("/api/v1/settings/profile/has_avatar")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.stored(), {"has_avatar": True})
        # Reading it is fine: it is the user's own.
        response = self.client.get("/api/v1/settings/profile/has_avatar")
        self.assertEqual(response.json()["value"], True)

    def test_a_refused_value_is_reported_under_value(self):
        response = self.put("bio", "x" * 201)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.json()), ["value"])

    def test_the_response_carries_what_was_stored(self):
        response = self.put("bio", "  hello  ")
        self.assertEqual(
            response.json(), {"module": "profile", "key": "bio", "value": "hello"}
        )

    def test_a_null_or_missing_value_removes_the_setting(self):
        for body in ({"value": None}, {}):
            with self.subTest(body=body):
                self.put("bio", "hello")
                response = self.client.put(
                    "/api/v1/settings/profile/bio",
                    data=body,
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json()["value"])
                self.assertFalse(UserSetting.objects.filter(user=self.user).exists())

    def test_a_removed_setting_reads_as_its_default(self):
        # A null used to be stored, and read back as None where the reader
        # asked for a dict: voting on a poll answered 500 for its creator.
        self.put("preferences", {"notifyPollVotes": False}, "calendar")
        self.put("preferences", None, "calendar")
        preferences = get_setting(self.user, "calendar", "preferences", default={})
        self.assertEqual(preferences, {})

    def test_a_body_that_is_not_an_object_is_refused(self):
        for body in ("[1, 2]", '"dev"', "7"):
            with self.subTest(body=body):
                response = self.client.put(
                    "/api/v1/settings/profile/role",
                    data=body,
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stored(), {})

    def test_the_bulk_write_names_each_refused_key(self):
        response = self.client.patch(
            "/api/v1/settings/profile",
            data={"role": "dev", "bio": "x" * 201, "shoe_size": 42},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.json()), ["shoe_size"])
        response = self.client.patch(
            "/api/v1/settings/profile",
            data={"role": "dev", "bio": "x" * 201},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.json()), ["bio"])
        self.assertEqual(self.stored(), {})

    def test_the_bulk_write_of_an_unknown_module_is_not_found(self):
        response = self.client.patch(
            "/api/v1/settings/attic",
            data={"bio": "hello"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_the_bulk_write_removes_a_null_key(self):
        self.put("bio", "hello")
        response = self.client.patch(
            "/api/v1/settings/profile",
            data={"bio": None, "role": "dev"},
            content_type="application/json",
        )
        self.assertEqual(response.json(), {"role": "dev"})


class ProfileSettingsTests(SettingsApiTestCase):
    module = "profile"

    def test_a_bio_and_a_role_within_their_limits_are_stored(self):
        self.assertAccepts("bio", ["x" * 200])
        self.assertAccepts("role", ["x" * 50])

    def test_a_bio_or_a_role_past_its_limit_is_refused(self):
        self.assertRefuses("bio", ["x" * 201, ["a"], {"a": 1}])
        self.assertRefuses("role", ["x" * 51, ["a"], True])

    def test_a_banner_is_a_preset_or_three_hex_colours(self):
        custom = {"from": "#112233", "via": "#445566", "to": "#778899"}
        self.assertAccepts("banner_palette", ["ocean", custom])
        self.assertRefuses(
            "banner_palette",
            [
                "no-such-preset",
                {"from": "#112233", "via": "#445566"},
                {"from": "red", "via": "#445566", "to": "#778899"},
                {"from": 1, "via": 2, "to": 3},
                7,
            ],
        )

    def test_a_custom_banner_keeps_its_three_colours_only(self):
        custom = {"from": "#112233", "via": "#445566", "to": "#778899"}
        self.put("banner_palette", {**custom, "angle": 90})
        self.assertEqual(self.stored()["banner_palette"], custom)
