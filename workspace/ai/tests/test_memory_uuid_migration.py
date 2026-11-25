"""The migration that gives UserMemory a UUID primary key keeps every memory.

Run through the migration executor on the real schema: the rows move to a new
table, so what is worth pinning is that each one arrives whole, timestamps
included, and comes back the same way when the migration is reversed.
"""

import uuid
from datetime import UTC, datetime

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("ai", "0025_aitask_answer_figures")]
AFTER = [("ai", "0026_usermemory_uuid_pk")]

CREATED = datetime(2025, 3, 1, 9, 30, tzinfo=UTC)
UPDATED = datetime(2025, 6, 2, 18, 5, tzinfo=UTC)


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    executor.loader.build_graph()
    return executor.loader.project_state(targets).apps


def _latest():
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


class UserMemoryUuidMigrationTests(TransactionTestCase):
    def setUp(self):
        old_apps = _migrate(BEFORE)
        user_model = old_apps.get_model("auth", "User")
        memory_model = old_apps.get_model("ai", "UserMemory")
        self.alice = user_model.objects.create(username="alice")
        self.nova = user_model.objects.create(username="nova")
        self.orbit = user_model.objects.create(username="orbit")
        for bot, key, content in (
            (self.nova, "name", "Alice"),
            (self.nova, "drink", "Coffee, no sugar"),
            (self.orbit, "name", "Ally"),
        ):
            memory_model.objects.create(
                user=self.alice, bot=bot, key=key, content=content
            )
        memory_model.objects.update(created_at=CREATED, updated_at=UPDATED)

    def tearDown(self):
        _migrate(_latest())

    def _rows(self, memory_model):
        return sorted(
            memory_model.objects.values_list(
                "user_id", "bot_id", "key", "content", "created_at", "updated_at"
            )
        )

    def _expected(self):
        return sorted(
            (self.alice.pk, bot.pk, key, content, CREATED, UPDATED)
            for bot, key, content in (
                (self.nova, "name", "Alice"),
                (self.nova, "drink", "Coffee, no sugar"),
                (self.orbit, "name", "Ally"),
            )
        )

    def test_every_memory_survives_with_a_uuid_key(self):
        memory_model = _migrate(AFTER).get_model("ai", "UserMemory")

        self.assertEqual(self._rows(memory_model), self._expected())
        keys = list(memory_model.objects.values_list("pk", flat=True))
        self.assertTrue(all(isinstance(key, uuid.UUID) for key in keys))
        self.assertEqual(len(set(keys)), 3)

    def test_a_key_stays_unique_per_user_and_bot(self):
        memory_model = _migrate(AFTER).get_model("ai", "UserMemory")

        with self.assertRaises(IntegrityError), transaction.atomic():
            memory_model.objects.create(
                user_id=self.alice.pk, bot_id=self.nova.pk, key="name", content="x"
            )

    def test_reversing_puts_every_memory_back(self):
        _migrate(AFTER)
        memory_model = _migrate(BEFORE).get_model("ai", "UserMemory")

        self.assertEqual(self._rows(memory_model), self._expected())
