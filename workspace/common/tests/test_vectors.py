import sqlite3
import tempfile
import threading
import time
import uuid
from io import StringIO
from pathlib import Path
from unittest import mock

import numpy as np
from django.core.management import call_command
from django.db import connection, connections, models, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.test.utils import isolate_apps

from workspace.common.uuids import uuid_v7_or_v4
from workspace.common.vectors import active_backend, checks, indexing, nearest
from workspace.common.vectors import sqlite as sqlite_backend
from workspace.common.vectors.encoding import from_bytes, normalize
from workspace.common.vectors.fallback import NumpyNearest
from workspace.common.vectors.indexing import (
    drop_vector,
    index_vector,
    rebuild_vector_index,
)
from workspace.common.vectors.operations import RunVectorIndexSQL
from workspace.common.vectors.schema import VectorIndex

TABLE = "common_vector_fixture"
DIMS = 8

VECTORS = VectorIndex(
    table=TABLE,
    dims=DIMS,
    source_column="embedding",
    partition_column="owner_id",
)

GLOBAL_VECTORS = VectorIndex(table=TABLE, dims=DIMS, source_column="embedding")

OWNER, OTHER = 1, 2


def _create_table(cursor):
    if connection.vendor == "postgresql":
        cursor.execute(
            f"CREATE TABLE {TABLE} (uuid uuid PRIMARY KEY, owner_id integer, "
            f"embedding bytea)"
        )
    else:
        cursor.execute(
            f"CREATE TABLE {TABLE} (uuid char(32) PRIMARY KEY, owner_id integer, "
            f"embedding blob)"
        )


def _migration(index):
    """The operation a migration would carry, built from vector_sql's blocks."""
    return RunVectorIndexSQL(
        pg_forward=index.pg_forward_sql(),
        pg_backfill=index.pg_backfill,
        pg_index=index.pg_index_sql(),
        pg_reverse=index.pg_reverse_sql(),
        sqlite_forward=index.sqlite_forward_sql(),
        sqlite_reverse=index.sqlite_reverse_sql(),
    )


def _without_sqlite_vec():
    return mock.patch.dict(
        "workspace.common.vectors._sqlite_vec_cache", {connection.alias: False}
    )


def _fallback():
    return mock.patch(
        "workspace.common.vectors.active_backend", return_value=NumpyNearest()
    )


def _fixture_vectors(count, seed=7):
    rng = np.random.default_rng(seed)
    return [rng.normal(size=DIMS) for _ in range(count)]


def _derived_index_available():
    """Whether the forward SQL creates a derived structure on this database."""
    if connection.vendor == "sqlite":
        return sqlite_backend.sqlite_vec_loaded(connection)
    if connection.vendor == "postgresql":
        from workspace.common.vectors.postgres import (
            pgvector_available,
            pgvector_version,
        )

        if not pgvector_available(connection):
            return False
        if pgvector_version(connection) is not None:
            return True
        # pgvector is not a trusted extension: only a superuser enables it.
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            return cursor.fetchone()[0]
    return False


class _FixtureTestCase(TestCase):
    """A table shaped like a real indexed model, with its derived index."""

    def setUp(self):
        with connection.cursor() as cursor:
            _create_table(cursor)
        rebuild_vector_index(VECTORS)

    def _row(self, owner=OWNER):
        pk = uuid.uuid4()
        param = pk if connection.vendor == "postgresql" else pk.hex
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {TABLE} (uuid, owner_id) VALUES (%s, %s)", [param, owner]
            )
        return pk

    def _rows_with_vectors(self, vectors, owner=OWNER):
        pks = []
        for vector in vectors:
            pk = self._row(owner)
            index_vector(VECTORS, pk, vector)
            pks.append(pk)
        return pks

    def _stored(self, pk):
        param = pk if connection.vendor == "postgresql" else pk.hex
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT embedding FROM {TABLE} WHERE uuid = %s", [param])
            row = cursor.fetchone()
        return None if row is None else row[0]

    def _vec_keys(self):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT uuid FROM {VECTORS.vec_table}")
            return {uuid.UUID(row[0]) for row in cursor.fetchall()}

    def _require_derived_index(self):
        if not _derived_index_available():
            self.skipTest("sqlite-vec or pgvector required")


class NearestArgumentTests(SimpleTestCase):
    def test_partitioned_index_requires_a_partition(self):
        with self.assertRaises(ValueError):
            nearest(VECTORS, [1] * DIMS, k=3)

    def test_unpartitioned_index_refuses_one(self):
        with self.assertRaises(ValueError):
            nearest(GLOBAL_VECTORS, [1] * DIMS, partition=OWNER, k=3)

    def test_k_is_bounded(self):
        for k in (0, 4097, 2.0, True):
            with self.subTest(k=k), self.assertRaises(ValueError):
                nearest(VECTORS, [1] * DIMS, partition=OWNER, k=k)

    def test_query_of_the_wrong_size_is_refused(self):
        with self.assertRaises(ValueError):
            nearest(VECTORS, [1] * (DIMS + 1), partition=OWNER, k=3)


class WriteTests(_FixtureTestCase):
    def test_the_source_column_holds_the_normalized_float32(self):
        pk = self._row()
        index_vector(VECTORS, pk, [2] + [0] * (DIMS - 1))
        blob = self._stored(pk)
        np.testing.assert_array_equal(from_bytes(blob, DIMS), [1] + [0] * (DIMS - 1))

    def test_drop_clears_the_vector(self):
        pk = self._row()
        index_vector(VECTORS, pk, _fixture_vectors(1)[0])
        drop_vector(VECTORS, pk)
        self.assertIsNone(self._stored(pk))
        self.assertEqual(
            nearest(VECTORS, _fixture_vectors(1)[0], partition=OWNER, k=5), []
        )

    def test_unknown_pk_writes_nothing(self):
        index_vector(VECTORS, uuid.uuid4(), _fixture_vectors(1)[0])
        drop_vector(VECTORS, uuid.uuid4())
        self.assertEqual(
            nearest(VECTORS, _fixture_vectors(1)[0], partition=OWNER, k=5), []
        )

    def test_wrong_size_is_refused_before_any_write(self):
        pk = self._row()
        with self.assertRaises(ValueError):
            index_vector(VECTORS, pk, [1, 2])
        self.assertIsNone(self._stored(pk))


class NearestTests(_FixtureTestCase):
    """Behaviour every backend shares, run on whichever one is active."""

    def test_closest_first_with_distances(self):
        vectors = _fixture_vectors(6)
        pks = self._rows_with_vectors(vectors)
        results = nearest(VECTORS, vectors[2], partition=OWNER, k=3)
        self.assertEqual(results[0][0], pks[2])
        self.assertAlmostEqual(results[0][1], 0.0, places=5)
        distances = [d for _, d in results]
        self.assertEqual(distances, sorted(distances))
        self.assertEqual(len(results), 3)

    def test_other_partitions_are_never_searched(self):
        vectors = _fixture_vectors(4)
        mine = self._rows_with_vectors(vectors[:2], owner=OWNER)
        self._rows_with_vectors(vectors[2:], owner=OTHER)
        results = nearest(VECTORS, vectors[3], partition=OWNER, k=10)
        self.assertEqual({pk for pk, _ in results}, set(mine))

    def test_fewer_rows_than_k(self):
        vectors = _fixture_vectors(2)
        self._rows_with_vectors(vectors)
        self.assertEqual(len(nearest(VECTORS, vectors[0], partition=OWNER, k=10)), 2)

    def test_reindexing_moves_the_row(self):
        near, far = [1] + [0] * (DIMS - 1), [0] * (DIMS - 1) + [1]
        pk = self._rows_with_vectors([near])[0]
        index_vector(VECTORS, pk, far)
        [(found, distance)] = nearest(VECTORS, far, partition=OWNER, k=1)
        self.assertEqual(found, pk)
        self.assertAlmostEqual(distance, 0.0, places=5)


class BackendParityTests(_FixtureTestCase):
    """The indexed backend and the fallback agree on the top k."""

    def test_same_top_k_as_the_fallback(self):
        self._require_derived_index()
        vectors = _fixture_vectors(40)
        self._rows_with_vectors(vectors[:30], owner=OWNER)
        self._rows_with_vectors(vectors[30:], owner=OTHER)
        self.assertNotEqual(active_backend(VECTORS, connection).name, "numpy")

        for query in _fixture_vectors(5, seed=99):
            indexed = nearest(VECTORS, query, partition=OWNER, k=5)
            with _fallback():
                exact = nearest(VECTORS, query, partition=OWNER, k=5)
            self.assertEqual([pk for pk, _ in indexed], [pk for pk, _ in exact])
            np.testing.assert_allclose(
                [d for _, d in indexed], [d for _, d in exact], atol=1e-5
            )


class SqliteVecTests(_FixtureTestCase):
    def setUp(self):
        if connection.vendor != "sqlite" or not sqlite_backend.sqlite_vec_loaded(
            connection
        ):
            self.skipTest("SQLite + sqlite-vec required")
        super().setUp()

    def test_the_entry_is_keyed_on_the_uuid(self):
        pk = self._rows_with_vectors(_fixture_vectors(1))[0]
        self.assertEqual(self._vec_keys(), {pk})

    def test_the_partition_is_the_rows_own(self):
        pk = self._rows_with_vectors(_fixture_vectors(1), owner=OWNER)[0]
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {TABLE} SET owner_id = %s WHERE uuid = %s", [OTHER, pk.hex]
            )
        # Stale until reindexed, which re-derives the partition from the row.
        blob = self._stored(pk)
        index_vector(VECTORS, pk, from_bytes(blob, DIMS))
        query = from_bytes(blob, DIMS)
        self.assertEqual(nearest(VECTORS, query, partition=OWNER, k=5), [])
        self.assertEqual(
            [p for p, _ in nearest(VECTORS, query, partition=OTHER, k=5)], [pk]
        )

    def test_drop_removes_the_vec0_entry(self):
        pk = self._rows_with_vectors(_fixture_vectors(1))[0]
        drop_vector(VECTORS, pk)
        self.assertEqual(self._vec_keys(), set())

    def test_drop_after_the_row_is_gone_still_removes_the_entry(self):
        # A post_delete receiver, or a cleanup after a bulk delete, only has
        # the pk left: the entry must not need the row to be found.
        pk = self._rows_with_vectors(_fixture_vectors(1))[0]
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {TABLE} WHERE uuid = %s", [pk.hex])
        drop_vector(VECTORS, pk)
        self.assertEqual(self._vec_keys(), set())

    def test_an_entry_whose_row_is_gone_is_not_returned(self):
        vectors = _fixture_vectors(2)
        gone, kept = self._rows_with_vectors(vectors)
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {TABLE} WHERE uuid = %s", [gone.hex])
        self.assertEqual(
            [pk for pk, _ in nearest(VECTORS, vectors[0], partition=OWNER, k=5)], [kept]
        )

    def test_orphans_do_not_take_the_k_slots(self):
        # Rows deleted behind drop_vector's back (a data migration through
        # apps.get_model, a process that could not load sqlite-vec) leave
        # entries that are, by construction, the query's nearest neighbours.
        query = _fixture_vectors(1, seed=3)[0]
        rng = np.random.default_rng(4)
        near = [query + rng.normal(scale=0.01, size=DIMS) for _ in range(5)]
        far = [-query + rng.normal(scale=0.5, size=DIMS) for _ in range(5)]
        gone = self._rows_with_vectors(near)
        kept = self._rows_with_vectors(far)
        with connection.cursor() as cursor:
            for pk in gone:
                cursor.execute(f"DELETE FROM {TABLE} WHERE uuid = %s", [pk.hex])

        results = nearest(VECTORS, query, partition=OWNER, k=5)

        self.assertEqual({pk for pk, _ in results}, set(kept))

    def test_rebuild_purges_orphans_and_indexes_missed_rows(self):
        vectors = _fixture_vectors(3)
        gone, kept = self._rows_with_vectors(vectors[:2])
        # Written while sqlite-vec was missing: a source, no entry.
        late = self._row()
        with _without_sqlite_vec():
            index_vector(VECTORS, late, vectors[2])
        self.assertEqual(self._vec_keys(), {gone, kept})
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {TABLE} WHERE uuid = %s", [gone.hex])

        self.assertEqual(rebuild_vector_index(VECTORS), "sqlite-vec")

        self.assertEqual(self._vec_keys(), {kept, late})
        [(found, _)] = nearest(VECTORS, vectors[2], partition=OWNER, k=1)
        self.assertEqual(found, late)

    def _set_source(self, pk, blob):
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {TABLE} SET embedding = %s WHERE uuid = %s", [blob, pk.hex]
            )

    def test_rebuild_skips_vectors_of_the_wrong_size(self):
        # One truncated blob, one left by an embedding model of another size:
        # vec0 refuses both, and one refusal must not abort the whole INSERT.
        valid, truncated, resized = self._rows_with_vectors(_fixture_vectors(3))
        self._set_source(truncated, b"\x00" * 3)
        self._set_source(resized, b"\x00" * 4 * (DIMS + 1))

        self.assertEqual(rebuild_vector_index(VECTORS), "sqlite-vec")

        self.assertEqual(self._vec_keys(), {valid})

    def test_a_row_whose_source_lost_its_shape_is_not_returned(self):
        # The fallback skips a blob of the wrong size; so must the index,
        # whatever entry it still holds for the row.
        vectors = _fixture_vectors(2)
        broken, kept = self._rows_with_vectors(vectors)
        self._set_source(broken, b"\x00" * 3)
        results = nearest(VECTORS, vectors[0], partition=OWNER, k=5)
        self.assertEqual([pk for pk, _ in results], [kept])

    def test_a_small_partition_does_not_reserve_a_large_chunk(self):
        # Every partition value gets its own chunk, zero-filled to chunk_size
        # vectors and never reclaimed: at sqlite-vec's default of 1024 that is
        # 2 MB per user at 512 dimensions, whatever they actually store.
        def pages():
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA page_count")
                return cursor.fetchone()[0]

        before = pages()
        for owner, vector in enumerate(_fixture_vectors(50)):
            self._rows_with_vectors([vector], owner=owner)
        self.assertLess(pages() - before, 150)

    def test_a_zero_vector_ranks_last_instead_of_crashing(self):
        # index_vector never writes one, but a raw write can: sqlite-vec's
        # cosine distance to it is NaN, which comes back NULL and sorts first.
        vectors = _fixture_vectors(3)
        pks = self._rows_with_vectors(vectors[:2])
        zero = self._row()
        self._set_source(zero, b"\x00" * 4 * DIMS)
        rebuild_vector_index(VECTORS)

        results = nearest(VECTORS, vectors[0], partition=OWNER, k=3)

        self.assertEqual([pk for pk, _ in results][:2], [pks[0], pks[1]])
        self.assertEqual(results[2][0], zero)
        self.assertEqual(results[2][1], float("inf"))

    def test_rebuild_creates_a_table_the_migration_could_not(self):
        with connection.cursor() as cursor:
            cursor.execute(VECTORS.sqlite_reverse_sql())
        pk = self._row()
        index_vector(VECTORS, pk, _fixture_vectors(1)[0])
        self.assertEqual(active_backend(VECTORS, connection).name, "numpy")

        out = StringIO()
        call_command(
            "rebuild_vector_index",
            "workspace.common.tests.test_vectors.VECTORS",
            stdout=out,
        )

        self.assertIn(f"{TABLE}.embedding: sqlite-vec", out.getvalue())
        self.assertEqual(self._vec_keys(), {pk})


class PgvectorTests(_FixtureTestCase):
    """The pgvector backend, against a real server (the CI pgvector job)."""

    def setUp(self):
        if connection.vendor != "postgresql" or not _derived_index_available():
            self.skipTest("PostgreSQL + pgvector required")
        super().setUp()
        self.assertEqual(active_backend(VECTORS, connection).name, "pgvector")

    @staticmethod
    def _version():
        from workspace.common.vectors.postgres import pgvector_version

        return pgvector_version(connection)

    @staticmethod
    def _set(setting):
        with connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL {setting}")

    @staticmethod
    def _show(setting):
        with connection.cursor() as cursor:
            cursor.execute(f"SHOW {setting}")
            return cursor.fetchone()[0]

    @staticmethod
    def _apply(operation, direction):
        with connection.schema_editor() as editor:
            getattr(operation, f"database_{direction}")("common", editor, None, None)

    def _pks(self, query, *, k=5, index=VECTORS, partition=OWNER):
        return [pk for pk, _ in nearest(index, query, partition=partition, k=k)]

    def test_replaying_the_migration_keeps_the_index_filled(self):
        vectors = _fixture_vectors(60)
        self._rows_with_vectors(vectors)
        query = _fixture_vectors(1, seed=11)[0]
        with _fallback():
            expected = self._pks(query)
        l2 = VectorIndex(
            table=TABLE,
            dims=DIMS,
            source_column="embedding",
            partition_column="owner_id",
            metric="l2",
        )
        scenarios = {
            "same forward again": [(_migration(VECTORS), "forwards")],
            "a later migration changing the metric": [(_migration(l2), "forwards")],
            "rollback then re-apply": [
                (_migration(VECTORS), "backwards"),
                (_migration(VECTORS), "forwards"),
            ],
        }
        for name, steps in scenarios.items():
            with self.subTest(name):
                for operation, direction in steps:
                    self._apply(operation, direction)
                self.assertEqual(self._pks(query), expected)

    def test_a_small_partition_among_nearer_rows_gets_its_k_results(self):
        # An HNSW scan filters only the tuples it visits (hnsw.max_scan_tuples
        # with iterative scans, 20,000 by default; lowered here to keep the
        # fixture small). When every visited tuple belongs to someone else, a
        # scan-and-filter answers nothing.
        if (self._version() or ()) < (0, 8):
            self.skipTest("pgvector 0.8+ required")
        query = _fixture_vectors(1, seed=5)[0]
        rng = np.random.default_rng(6)
        self._rows_with_vectors(
            [query + rng.normal(scale=0.05, size=DIMS) for _ in range(300)],
            owner=OTHER,
        )
        mine = self._rows_with_vectors(
            [-query + rng.normal(scale=0.5, size=DIMS) for _ in range(20)]
        )
        self._set("hnsw.max_scan_tuples = 50")
        self._set("enable_seqscan = off")

        found = self._pks(query)

        self.assertEqual(len(found), 5)
        self.assertLessEqual(set(found), set(mine))

    def test_a_global_query_returns_k_rows_past_ef_search(self):
        # An HNSW scan stops at hnsw.ef_search rows (40) unless told otherwise.
        vectors = _fixture_vectors(150)
        self._rows_with_vectors(vectors)
        self._set("enable_seqscan = off")
        self.assertEqual(
            len(self._pks(vectors[0], k=100, index=GLOBAL_VECTORS, partition=None)), 100
        )

        with mock.patch.dict(
            "workspace.common.vectors._pgvector_version_cache",
            {connection.alias: (0, 6, 0)},
        ):
            found = self._pks(vectors[0], k=100, index=GLOBAL_VECTORS, partition=None)
        self.assertEqual(len(found), 100)

    def test_a_row_whose_source_was_cleared_is_not_returned(self):
        # Cleared outside drop_vector - .update(embedding=None), a stale save,
        # a data migration: the vector column still holds the old vector.
        vectors = _fixture_vectors(2)
        cleared, kept = self._rows_with_vectors(vectors)
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {TABLE} SET embedding = NULL WHERE uuid = %s", [cleared]
            )
        self.assertEqual(self._pks(vectors[0]), [kept])

    def test_no_scan_setting_outlives_the_query(self):
        # Inside a caller's atomic() the query's own block is a savepoint,
        # and releasing one keeps what SET LOCAL did.
        vectors = _fixture_vectors(60)
        self._rows_with_vectors(vectors)
        defaults = {
            s: self._show(s)
            for s in ("enable_indexscan", "hnsw.iterative_scan", "hnsw.ef_search")
        }
        with transaction.atomic():
            self._pks(vectors[0])
            self._pks(vectors[0], k=50, index=GLOBAL_VECTORS, partition=None)
            with mock.patch.dict(
                "workspace.common.vectors._pgvector_version_cache",
                {connection.alias: (0, 6, 0)},
            ):
                self._pks(vectors[0], k=50, index=GLOBAL_VECTORS, partition=None)
            self.assertEqual({s: self._show(s) for s in defaults}, defaults)


class ScanSettingTests(SimpleTestCase):
    """Which HNSW setting a query gets, per pgvector version."""

    def _setting(self, version, index, k):
        from workspace.common.vectors.postgres import PgvectorNearest

        return PgvectorNearest(version)._scan_setting(index, k)

    def test_a_partitioned_query_is_exact_whatever_the_version(self):
        for version in ((0, 6, 0), (0, 8, 1)):
            with self.subTest(version=version):
                self.assertIsNone(self._setting(version, VECTORS, 5))

    def test_a_global_query_scans_iteratively_from_0_8(self):
        self.assertIn("iterative_scan", self._setting((0, 8, 1), GLOBAL_VECTORS, 4096))

    def test_before_0_8_ef_search_follows_k_up_to_its_ceiling(self):
        self.assertIsNone(self._setting((0, 6, 0), GLOBAL_VECTORS, 40))
        self.assertEqual(
            self._setting((0, 6, 0), GLOBAL_VECTORS, 100),
            "SET LOCAL hnsw.ef_search = 100",
        )
        self.assertIsNone(self._setting((0, 6, 0), GLOBAL_VECTORS, 1001))


class FallbackTests(_FixtureTestCase):
    def test_without_sqlite_vec_the_source_column_is_scanned(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite only")
        vectors = _fixture_vectors(5)
        with _without_sqlite_vec():
            pks = self._rows_with_vectors(vectors)
            self.assertEqual(active_backend(VECTORS, connection).name, "numpy")
            results = nearest(VECTORS, vectors[4], partition=OWNER, k=2)
            self.assertEqual(rebuild_vector_index(VECTORS), "numpy")
        self.assertEqual(results[0][0], pks[4])

    def test_an_unreadable_blob_is_skipped(self):
        vectors = _fixture_vectors(2)
        pks = self._rows_with_vectors(vectors)
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {TABLE} SET embedding = %s WHERE uuid = %s",
                [
                    b"\x00" * 3,
                    pks[0] if connection.vendor == "postgresql" else pks[0].hex,
                ],
            )
        with _fallback():
            results = nearest(VECTORS, vectors[0], partition=OWNER, k=5)
        self.assertEqual([pk for pk, _ in results], [pks[1]])


class FallbackMathTests(SimpleTestCase):
    """The ranking itself, fed rows directly instead of through a table."""

    @staticmethod
    def _rank(index, rows, query, k, block=2048):
        cursor = mock.MagicMock()
        cursor.fetchmany.side_effect = [
            *(rows[i : i + block] for i in range(0, len(rows), block)),
            [],
        ]
        conn = mock.MagicMock(vendor="sqlite")
        conn.chunked_cursor.return_value.__enter__.return_value = cursor
        with mock.patch("workspace.common.vectors.fallback._BLOCK", block):
            return NumpyNearest().nearest(
                index, conn, normalize(query, index.dims), partition=None, k=k
            )

    def test_blocks_rank_like_a_single_pass(self):
        # Only the running top k survives each block: the merge must not
        # lose a row a later block would have ranked lower.
        index = VectorIndex(table="t", dims=DIMS, source_column="e")
        rows = [
            (i, normalize(vector, DIMS).tobytes())
            for i, vector in enumerate(_fixture_vectors(50))
        ]
        query = _fixture_vectors(1, seed=21)[0]
        single = self._rank(index, rows, query, k=7)
        for block in (1, 3, 7, 8, 49):
            with self.subTest(block=block):
                blocked = self._rank(index, rows, query, k=7, block=block)
                self.assertEqual([pk for pk, _ in blocked], [pk for pk, _ in single])
                # float32 products sum differently per block size.
                np.testing.assert_allclose(
                    [d for _, d in blocked], [d for _, d in single], atol=1e-6
                )

    def test_l2_metric(self):
        index = VectorIndex(table="t", dims=2, source_column="e", metric="l2")
        rows = [
            ("a", normalize([1, 0], 2).tobytes()),
            ("b", normalize([0, 1], 2).tobytes()),
        ]
        results = self._rank(index, rows, [1, 0], k=2)
        self.assertEqual([pk for pk, _ in results], ["a", "b"])
        self.assertAlmostEqual(results[1][1], float(np.sqrt(2)), places=5)

    def test_a_zero_vector_ranks_last_instead_of_poisoning_the_ranking(self):
        index = VectorIndex(table="t", dims=2, source_column="e")
        rows = [
            ("zero", np.zeros(2, dtype="<f4").tobytes()),
            ("far", normalize([-1, 0], 2).tobytes()),
            ("near", normalize([1, 0.1], 2).tobytes()),
        ]
        results = self._rank(index, rows, [1, 0], k=3)
        self.assertEqual([pk for pk, _ in results], ["near", "far", "zero"])


@isolate_apps("workspace.common")
class SurvivesATableRebuildTests(TransactionTestCase):
    """The vec0 index must outlive a migration that rewrites the base table.

    Django's SQLite schema editor rebuilds a table for an AddField with a
    default (create-copy-drop-rename), and the copy does not carry implicit
    rowids across. An index keyed on them keeps answering, with the wrong rows.
    TransactionTestCase because the schema editor cannot run inside the test's
    transaction on SQLite.
    """

    def setUp(self):
        if connection.vendor != "sqlite" or not sqlite_backend.sqlite_vec_loaded(
            connection
        ):
            self.skipTest("SQLite + sqlite-vec required")

        class Face(models.Model):
            uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4)
            owner_id = models.IntegerField(null=True)
            embedding = models.BinaryField(null=True)

            class Meta:
                app_label = "common"
                db_table = TABLE

            def __str__(self):
                return str(self.uuid)

        self.Face = Face
        with connection.schema_editor() as editor:
            editor.create_model(Face)
        rebuild_vector_index(VECTORS)

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute(VECTORS.sqlite_reverse_sql())
        with connection.schema_editor() as editor:
            editor.delete_model(self.Face)

    def _implicit_rowids(self):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT uuid, rowid FROM {TABLE} ORDER BY uuid")
            return dict(cursor.fetchall())

    def test_nearest_still_finds_the_right_rows_after_an_add_field(self):
        vectors = _fixture_vectors(5)
        faces = [self.Face.objects.create(owner_id=OWNER) for _ in vectors]
        for face, vector in zip(faces, vectors, strict=True):
            index_vector(VECTORS, face.pk, vector)
        # Deleting the first row leaves a gap the copy closes, so every
        # remaining implicit rowid moves.
        drop_vector(VECTORS, faces[0].pk)
        faces[0].delete()
        before = self._implicit_rowids()

        field = models.BooleanField(default=False)
        field.set_attributes_from_name("reviewed")
        with connection.schema_editor() as editor:
            editor.add_field(self.Face, field)

        # Guards the guard: if the rowids had not moved, the assertions below
        # would pass for the wrong reason.
        self.assertNotEqual(before, self._implicit_rowids())
        for face, vector in zip(faces[1:], vectors[1:], strict=True):
            [(found, _)] = nearest(VECTORS, vector, partition=OWNER, k=1)
            self.assertEqual(found, face.pk)


class RunVectorIndexSQLTests(TransactionTestCase):
    """The migration applies with and without the extension, both ways."""

    def setUp(self):
        with connection.cursor() as cursor:
            _create_table(cursor)

    def tearDown(self):
        with connection.cursor() as cursor:
            if connection.vendor == "sqlite" and sqlite_backend.sqlite_vec_loaded(
                connection
            ):
                cursor.execute(VECTORS.sqlite_reverse_sql())
            cursor.execute(f"DROP TABLE {TABLE}")

    def _apply(self, direction):
        with connection.schema_editor() as editor:
            method = getattr(_migration(VECTORS), f"database_{direction}")
            method("common", editor, None, None)

    def _has_derived_structure(self):
        with connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                cursor.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = %s AND column_name = %s",
                    [TABLE, VECTORS.pg_column],
                )
            else:
                cursor.execute(
                    "SELECT 1 FROM sqlite_master WHERE name = %s", [VECTORS.vec_table]
                )
            return cursor.fetchone() is not None

    def test_forward_and_backward_with_the_extension(self):
        if not _derived_index_available():
            self.skipTest("sqlite-vec or pgvector required")
        self._apply("forwards")
        self.assertTrue(self._has_derived_structure())
        self._apply("backwards")
        self.assertFalse(self._has_derived_structure())

    def test_forward_skips_rows_left_at_another_size(self):
        # Switching embedding model is a schema change plus a reindex: the
        # migration for the new size meets rows still at the old one.
        if connection.vendor != "sqlite" or not _derived_index_available():
            self.skipTest("SQLite + sqlite-vec required")
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {TABLE} (uuid, owner_id, embedding) VALUES (%s, %s, %s)",
                [uuid.uuid4().hex, OWNER, b"\x00" * 4 * (DIMS - 1)],
            )
        self._apply("forwards")
        self.assertTrue(self._has_derived_structure())

    def test_forward_and_backward_without_sqlite_vec(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite only")
        with mock.patch(
            "workspace.common.vectors.operations.sqlite_vec_loaded", return_value=False
        ):
            self._apply("forwards")
            self.assertFalse(self._has_derived_structure())
            self._apply("backwards")

    def test_deconstructs_to_its_sql(self):
        operation = _migration(VECTORS)
        name, args, kwargs = operation.deconstruct()
        self.assertEqual(name, "RunVectorIndexSQL")
        self.assertEqual(kwargs["sqlite_forward"], VECTORS.sqlite_forward_sql())
        self.assertIn("vector", operation.describe())


class WriteDuringRebuildTests(TransactionTestCase):
    """A write racing a rebuild must land in the index the rebuild creates.

    The scenario the docs prescribe: install sqlite-vec or pgvector, then run
    rebuild_vector_index on a live instance. Two real connections, because
    only the database's own locking decides the interleaving: a writer that
    picks its backend before taking the write lock sees the index missing,
    waits for the rebuild to commit, and writes the source alone.
    """

    ALIAS = "vectors_race"

    def setUp(self):
        if not _derived_index_available():
            self.skipTest("sqlite-vec or pgvector required")
        settings = dict(connection.settings_dict)
        if connection.vendor == "sqlite":
            # The in-memory test database uses shared-cache table locks, which
            # fail at once instead of waiting: a file gets real WAL locking.
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            settings["NAME"] = str(Path(tmp.name) / "race.sqlite3")
        connections.databases[self.ALIAS] = settings
        # Declared for this test only: the class attribute is validated against
        # settings.DATABASES before the alias exists.
        cls = type(self)
        cls.databases, saved = {*cls.databases, self.ALIAS}, cls.databases
        self.addCleanup(setattr, cls, "databases", saved)
        self.addCleanup(self._forget_alias)
        with connections[self.ALIAS].cursor() as cursor:
            _create_table(cursor)
        self.pk = uuid.uuid4()
        with connections[self.ALIAS].cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {TABLE} (uuid, owner_id) VALUES (%s, %s)",
                [self._param(self.pk), OWNER],
            )

    def _forget_alias(self):
        with connections[self.ALIAS].cursor() as cursor:
            if connection.vendor == "sqlite":
                cursor.execute(VECTORS.sqlite_reverse_sql())
            cursor.execute(f"DROP TABLE {TABLE}")
        connections[self.ALIAS].close()
        if hasattr(connections[self.ALIAS], "close_pool"):
            connections[self.ALIAS].close_pool()
        del connections.databases[self.ALIAS]

    def _param(self, pk):
        return pk if connection.vendor == "postgresql" else pk.hex

    def test_the_racing_write_reaches_the_new_index(self):
        created, release = threading.Event(), threading.Event()
        run_script = indexing._run_script
        errors = []

        def held_open(conn, sql):
            run_script(conn, sql)
            created.set()
            release.wait(10)

        def rebuild():
            try:
                with mock.patch.object(indexing, "_run_script", held_open):
                    rebuild_vector_index(VECTORS, using=self.ALIAS)
            except Exception as exc:
                errors.append(exc)
            finally:
                created.set()
                connections[self.ALIAS].close()

        def write():
            try:
                index_vector(VECTORS, self.pk, _fixture_vectors(1)[0], using=self.ALIAS)
            except Exception as exc:
                errors.append(exc)
            finally:
                connections[self.ALIAS].close()

        rebuilder = threading.Thread(target=rebuild)
        rebuilder.start()
        created.wait(10)
        writer = threading.Thread(target=write)
        writer.start()
        # Long enough for the writer to pick its backend and block on the
        # lock. Too short only lets a broken writer pass, never a sound one fail.
        time.sleep(0.3)
        release.set()
        rebuilder.join(20)
        writer.join(20)

        self.assertEqual(errors, [])
        self.assertEqual(self._indexed(), [self.pk])

    def _indexed(self):
        with connections[self.ALIAS].cursor() as cursor:
            if connection.vendor == "postgresql":
                cursor.execute(
                    f"SELECT uuid FROM {TABLE} WHERE {VECTORS.pg_column} IS NOT NULL"
                )
                return [row[0] for row in cursor.fetchall()]
            cursor.execute(f"SELECT uuid FROM {VECTORS.vec_table}")
            return [uuid.UUID(row[0]) for row in cursor.fetchall()]


class LoaderTests(SimpleTestCase):
    def test_a_failed_load_is_soft(self):
        conn = mock.Mock(vendor="sqlite")
        with mock.patch.object(
            sqlite_backend, "load_into", side_effect=sqlite3.OperationalError("nope")
        ):
            sqlite_backend.load_sqlite_vec(sender=None, connection=conn)

    def test_other_vendors_are_left_alone(self):
        with mock.patch.object(sqlite_backend, "load_into") as load:
            sqlite_backend.load_sqlite_vec(
                sender=None, connection=mock.Mock(vendor="postgresql")
            )
        load.assert_not_called()

    def test_loadable_reports_a_missing_wheel(self):
        with mock.patch.object(sqlite_backend, "load_into", side_effect=ImportError):
            self.assertFalse(sqlite_backend.sqlite_vec_loadable())


class FallbackCheckTests(SimpleTestCase):
    def _registered(self, indexes):
        return mock.patch.object(
            checks, "registered_vector_indexes", return_value=indexes
        )

    def _sqlite(self):
        return mock.patch.object(
            checks,
            "connections",
            mock.Mock(all=lambda: [mock.Mock(vendor="sqlite", alias="default")]),
        )

    def test_silent_when_nothing_is_registered(self):
        with (
            self._registered(()),
            self._sqlite(),
            mock.patch.object(checks, "sqlite_vec_loadable", return_value=False),
        ):
            self.assertEqual(checks.check_vector_backends(None), [])

    def test_warns_when_sqlite_vec_cannot_load(self):
        with (
            self._registered((VECTORS,)),
            self._sqlite(),
            mock.patch.object(checks, "sqlite_vec_loadable", return_value=False),
        ):
            warnings = checks.check_vector_backends(None)
        self.assertEqual([w.id for w in warnings], ["common.W002"])

    def test_silent_when_sqlite_vec_loads(self):
        with (
            self._registered((VECTORS,)),
            self._sqlite(),
            mock.patch.object(checks, "sqlite_vec_loadable", return_value=True),
        ):
            self.assertEqual(checks.check_vector_backends(None), [])

    def test_postgres_is_only_asked_when_the_check_may_query_it(self):
        conn = mock.Mock(vendor="postgresql", alias="default")
        with (
            self._registered((VECTORS,)),
            mock.patch.object(checks, "connections", mock.Mock(all=lambda: [conn])),
            mock.patch.object(
                checks, "pgvector_available", return_value=False
            ) as probe,
        ):
            self.assertEqual(checks.check_vector_backends(None), [])
            probe.assert_not_called()
            warnings = checks.check_vector_backends(None, databases=["default"])
        self.assertEqual([w.id for w in warnings], ["common.W002"])
