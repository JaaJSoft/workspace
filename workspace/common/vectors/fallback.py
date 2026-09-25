from contextlib import nullcontext

import numpy as np
from django.db import transaction

# Rows ranked per round trip. Memory stays at one block plus the running top k
# whatever the partition's size.
_BLOCK = 2048


class NumpyNearest:
    """Exact brute force over the partition's source column.

    A real backend, not a stub: the vectors are already float32, so each
    block is one matrix product. 20,000 vectors of 512 dimensions rank in
    about 50 ms once fetched. The partition is read one block at a time and
    only the best k survive each block: on PostgreSQL a plain fetch holds the
    whole result, hex-encoded, several times the size of the vectors.
    """

    name = "numpy"

    def nearest(self, index, conn, query, *, partition, k):
        params = [partition] if index.partitioned else []
        width = index.dims * 4
        best_pks, best = [], np.empty(0, dtype=np.float64)
        # A server-side cursor needs a transaction, or PostgreSQL materializes
        # it WITH HOLD - the whole result again, server side.
        in_transaction = (
            transaction.atomic(using=conn.alias)
            if conn.vendor == "postgresql"
            else nullcontext()
        )
        with in_transaction, conn.chunked_cursor() as cursor:
            cursor.execute(index.source_rows_sql(), params)
            while block := cursor.fetchmany(_BLOCK):
                # A blob of the wrong size cannot be a vector of this index;
                # skipping it beats one corrupt row failing every search of
                # its partition. (PostgreSQL returns memoryviews; len() is in
                # bytes.)
                block = [(pk, blob) for pk, blob in block if len(blob) == width]
                if not block:
                    continue
                distances = self._distances(index, block, query)
                best_pks += [pk for pk, _ in block]
                best = np.concatenate([best, distances])
                if len(best) > k:
                    keep = np.argpartition(best, k - 1)[:k]
                    best_pks = [best_pks[i] for i in keep]
                    best = best[keep]
        order = np.argsort(best, kind="stable")
        return [(best_pks[i], float(best[i])) for i in order]

    @staticmethod
    def _distances(index, block, query):
        # One buffer, one copy: per-row arrays would dominate the cost.
        matrix = np.frombuffer(b"".join(blob for _, blob in block), dtype="<f4")
        matrix = matrix.reshape(len(block), index.dims)
        if index.metric == "cosine":
            norms = np.sqrt(np.einsum("ij,ij->i", matrix, matrix))
            norms *= np.linalg.norm(query)
            # A zero vector has no direction: it ranks last instead of NaN
            # poisoning the partition below. index_vector never writes one.
            with np.errstate(divide="ignore", invalid="ignore"):
                distances = 1.0 - (matrix @ query) / norms
            distances[~np.isfinite(distances)] = np.inf
            return distances.astype(np.float64)
        return np.linalg.norm(matrix - query, axis=1).astype(np.float64)
