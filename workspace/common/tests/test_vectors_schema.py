from io import StringIO

import numpy as np
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from workspace.common.vectors.encoding import (
    from_bytes,
    normalize,
    pg_literal,
    to_bytes,
)
from workspace.common.vectors.schema import (
    MAX_DIMS,
    VectorIndex,
    register_vector_index,
    registered_vector_indexes,
)

FACES = VectorIndex(
    table="photos_face",
    dims=512,
    source_column="embedding",
    partition_column="owner_id",
)

GLOBAL_L2 = VectorIndex(
    table="photos_scene",
    dims=3,
    source_column="signature",
    metric="l2",
)


class DeclarationTests(SimpleTestCase):
    def test_derived_names(self):
        self.assertEqual(FACES.vec_table, "photos_face_embedding_vec")
        self.assertEqual(FACES.pg_column, "embedding_vec")
        self.assertEqual(FACES.hnsw_index, "photos_face_embedding_hnsw")

    def test_identifiers_are_validated(self):
        # Every name is interpolated into SQL.
        with self.assertRaises(ValueError):
            VectorIndex(table="x; DROP TABLE y", dims=3, source_column="e")
        with self.assertRaises(ValueError):
            VectorIndex(
                table="t",
                dims=3,
                source_column="e",
                partition_column="a b",
            )

    def test_dims_are_bounded(self):
        for dims in (0, MAX_DIMS + 1, 3.0, True):
            with self.subTest(dims=dims), self.assertRaises(ValueError):
                VectorIndex(table="t", dims=dims, source_column="e")

    def test_chunk_size_is_what_vec0_accepts(self):
        for chunk_size in (0, 12, 4104, True):
            with self.subTest(chunk_size=chunk_size), self.assertRaises(ValueError):
                VectorIndex(table="t", dims=3, source_column="e", chunk_size=chunk_size)

    def test_metric_and_partition_type_are_checked(self):
        with self.assertRaises(ValueError):
            VectorIndex(table="t", dims=3, source_column="e", metric="dot")
        with self.assertRaises(ValueError):
            VectorIndex(
                table="t",
                dims=3,
                source_column="e",
                partition_type="blob",
            )

    def test_registration_is_keyed_by_table_and_column(self):
        from workspace.common.vectors import schema

        saved = dict(schema._registered)
        self.addCleanup(
            lambda: (schema._registered.clear(), schema._registered.update(saved))
        )
        register_vector_index(FACES)
        register_vector_index(FACES)
        register_vector_index(GLOBAL_L2)
        self.assertEqual(
            [i for i in registered_vector_indexes() if i in (FACES, GLOBAL_L2)],
            [FACES, GLOBAL_L2],
        )


class PostgresSqlTests(SimpleTestCase):
    def test_forward_is_conditional_on_pgvector(self):
        sql = FACES.pg_forward_sql()
        self.assertIn("pg_available_extensions WHERE name = 'vector'", sql)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector", sql)
        self.assertIn("EXCEPTION WHEN insufficient_privilege", sql)

    def test_forward_keeps_a_column_of_the_right_size(self):
        # Replayed on a populated table (a later migration, a rollback then
        # re-apply, a rebuild), it must not throw the vectors away.
        sql = FACES.pg_forward_sql()
        self.assertIn("ADD COLUMN IF NOT EXISTS embedding_vec vector(512)", sql)
        self.assertIn("atttypmod <> 512", sql)
        self.assertFalse(sql.startswith(FACES.pg_reverse_sql()))

    def test_the_index_is_built_separately_and_tolerates_an_old_pgvector(self):
        sql = FACES.pg_index_sql()
        self.assertIn("USING hnsw (embedding_vec vector_cosine_ops)", sql)
        self.assertIn("EXCEPTION WHEN undefined_object", sql)
        self.assertNotIn("hnsw", FACES.pg_forward_sql().split("DROP INDEX")[0])

    def test_backfill_carries_literals(self):
        self.assertEqual(
            FACES.pg_backfill,
            {
                "table": "photos_face",
                "pk_column": "uuid",
                "source_column": "embedding",
                "column": "embedding_vec",
                "dims": 512,
            },
        )

    def test_reverse_leaves_the_extension_and_the_source(self):
        sql = FACES.pg_reverse_sql()
        self.assertNotIn("EXTENSION", sql)
        self.assertNotIn("COLUMN IF EXISTS embedding;", sql)

    def test_l2_uses_the_l2_operator_class(self):
        self.assertIn("vector_l2_ops", GLOBAL_L2.pg_index_sql())
        self.assertIn("<->", GLOBAL_L2.pg_nearest_sql(exact=False))

    def test_nearest_binds_vector_partition_then_k(self):
        for exact in (True, False):
            with self.subTest(exact=exact):
                self.assertEqual(FACES.pg_nearest_sql(exact=exact).count("%s"), 3)
                self.assertEqual(GLOBAL_L2.pg_nearest_sql(exact=exact).count("%s"), 2)

    def test_an_exact_query_is_fenced_from_the_hnsw_index(self):
        self.assertIn("AS MATERIALIZED", FACES.pg_nearest_sql(exact=True))
        self.assertNotIn("MATERIALIZED", FACES.pg_nearest_sql(exact=False))


class SqliteSqlTests(SimpleTestCase):
    def test_vec0_table_has_the_partition_key_and_metric(self):
        sql = FACES.sqlite_forward_sql()
        self.assertIn("CREATE VIRTUAL TABLE photos_face_embedding_vec USING vec0(", sql)
        self.assertIn("owner_id integer partition key", sql)
        self.assertIn("embedding float[512] distance_metric=cosine", sql)
        self.assertIn("chunk_size=64", sql)

    def test_unpartitioned_table_has_no_partition_key(self):
        sql = GLOBAL_L2.sqlite_forward_sql()
        self.assertNotIn("partition key", sql)
        self.assertIn("distance_metric=l2", sql)
        self.assertNotIn("%s", GLOBAL_L2.sqlite_nearest_sql().split("AND k = %s")[1])

    def test_forward_backfills_from_the_source_column(self):
        sql = FACES.sqlite_forward_sql()
        self.assertIn("SELECT uuid, owner_id, embedding FROM photos_face", sql)

    def test_insert_reads_the_vector_off_the_row(self):
        # The only bind is the pk: the vector and the partition are the row's.
        self.assertEqual(FACES.sqlite_insert_sql().count("%s"), 1)

    def test_vec0_is_keyed_on_the_uuid_not_the_implicit_rowid(self):
        self.assertIn("uuid text primary key", FACES.sqlite_forward_sql())
        self.assertIn("t.uuid = knn.uuid", FACES.sqlite_nearest_sql())


class VectorSqlCommandTests(SimpleTestCase):
    def test_prints_all_four_blocks(self):
        out = StringIO()
        call_command(
            "vector_sql", "workspace.common.tests.test_vectors_schema.FACES", stdout=out
        )
        text = out.getvalue()
        for marker in (
            "-- PG_FORWARD",
            "-- PG_REVERSE",
            "-- SQLITE_FORWARD",
            "-- SQLITE_REVERSE",
        ):
            self.assertIn(marker, text)
        self.assertIn("USING vec0(", text)
        self.assertIn("vector(512)", text)
        self.assertIn("-- PG_INDEX", text)
        self.assertIn("'column': 'embedding_vec'", text)

    def test_bad_path_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command("vector_sql", "workspace.nowhere.MISSING")

    def test_non_index_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command("vector_sql", "workspace.common.vectors.schema.MAX_K")


class EncodingTests(SimpleTestCase):
    def test_normalize_scales_to_unit_length(self):
        arr = normalize([3, 4], 2)
        self.assertEqual(arr.dtype, np.dtype("<f4"))
        np.testing.assert_allclose(arr, [0.6, 0.8], rtol=1e-6)

    def test_normalize_rejects_what_has_no_direction(self):
        for bad in ([0, 0], [1, float("nan")], [1, float("inf")], [1, 2, 3], [[1, 2]]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize(bad, 2)

    def test_bytes_round_trip_little_endian(self):
        arr = normalize([1, 2, 3], 3)
        blob = to_bytes(arr)
        self.assertEqual(len(blob), 12)
        self.assertEqual(blob, arr.astype("<f4").tobytes())
        np.testing.assert_array_equal(from_bytes(memoryview(blob), 3), arr)

    def test_from_bytes_rejects_the_wrong_size(self):
        self.assertIsNone(from_bytes(b"\x00" * 8, 3))

    def test_pg_literal_parses_back_to_the_same_float32(self):
        arr = normalize([1, 2, 3, 4, 5], 5)
        literal = pg_literal(arr)
        self.assertTrue(literal.startswith("[") and literal.endswith("]"))
        parsed = np.array([float(x) for x in literal[1:-1].split(",")], dtype="<f4")
        np.testing.assert_array_equal(parsed, arr)
