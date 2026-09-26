"""The vector index of face embeddings, one partition per owner.

``dims`` follows the configured backend, but the migration froze the size the
default backend produces (128). Switching to a backend of another size is a
rebuild plus a re-analysis - see docs/photos/README.md.
"""

from workspace.common.vectors.schema import VectorIndex

from .services.detection.registry import configured_dims

FACE_EMBEDDINGS = VectorIndex(
    table="photos_face",
    dims=configured_dims(),
    source_column="embedding",
    partition_column="owner_id",
    partition_type="integer",
)
