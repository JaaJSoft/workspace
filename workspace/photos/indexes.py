"""The vector indexes of the photo library, one partition per owner: face
embeddings, and whole-photo image embeddings. Two spaces, never compared.

Each ``dims`` follows its configured backend, but the migrations froze the
size the default backend produces (128 for faces, 768 for photos). Switching
to a backend of another size is a rebuild plus a re-analysis - see
docs/photos/README.md.
"""

from workspace.common.vectors.schema import VectorIndex

from .services.detection.registry import configured_dims
from .services.embedding.registry import configured_dims as configured_embedding_dims

FACE_EMBEDDINGS = VectorIndex(
    table="photos_face",
    dims=configured_dims(),
    source_column="embedding",
    partition_column="owner_id",
    partition_type="integer",
)

PHOTO_EMBEDDINGS = VectorIndex(
    table="photos_photoembedding",
    dims=configured_embedding_dims(),
    source_column="embedding",
    partition_column="owner_id",
    partition_type="integer",
)
