"""How a vector is stored: `dims` little-endian float32, scaled to unit length.

Unit length is what lets every backend agree: sqlite-vec, pgvector and numpy
each compute distances their own way, and normalizing once, on the way in,
leaves them nothing to disagree about but rounding.
"""

import numpy as np

_FLOAT32_LE = np.dtype("<f4")


def normalize(vector, dims):
    """*vector* as a unit-length float32 array of *dims* components.

    Raises ValueError for the wrong size, a non-finite component or a zero
    vector - none of which has a direction to compare.
    """
    arr = np.asarray(vector, dtype=np.float64)
    if arr.shape != (dims,):
        raise ValueError(
            f"expected a vector of {dims} components, got shape {arr.shape}"
        )
    if not np.isfinite(arr).all():
        raise ValueError("vector has a non-finite component")
    norm = np.linalg.norm(arr)
    if norm == 0:
        raise ValueError("cannot normalize a zero vector")
    return (arr / norm).astype(_FLOAT32_LE)


def to_bytes(arr):
    return np.asarray(arr, dtype=_FLOAT32_LE).tobytes()


def from_bytes(blob, dims):
    """The stored vector, or None when *blob* is not `dims` float32."""
    blob = bytes(blob)  # PostgreSQL hands back a memoryview
    if len(blob) != dims * _FLOAT32_LE.itemsize:
        return None
    return np.frombuffer(blob, dtype=_FLOAT32_LE)


def pg_literal(arr):
    """pgvector's text form, e.g. '[0.6,0.8]'.

    Each component is printed at float64 precision from its float32 value, so
    pgvector parses back the exact float32 the source column holds.
    """
    return "[" + ",".join(repr(float(x)) for x in arr) + "]"
