"""Row-volume counting for tests that guard against over-fetching.

``assertNumQueries`` pins how *many* queries a view runs and says nothing about
how *wide* they are. A single ``prefetch_related`` that hydrates every member of
every conversation keeps the query count flat while the row count grows with the
data, so the regression never shows up in a query-count assertion - only in
response time and memory. This counts the model instances a block builds out of
the database, which is the number that has to stay bounded.
"""

from contextlib import contextmanager
from unittest.mock import patch


class RowCount:
    """Running total of instances built from DB rows, exposed as ``count``."""

    def __init__(self):
        self.count = 0


@contextmanager
def count_rows(model):
    """Count the *model* instances hydrated from the database inside the block.

    Every queryset iteration goes through ``Model.from_db``, including the ones
    a ``prefetch_related`` or a ``select_related`` join runs, so the counter
    sees rows the caller never asked for by name.
    """
    original = model.from_db
    counter = RowCount()

    # Signature passed straight through: Django grows keyword arguments on
    # from_db between releases (``fetch_mode`` in 6.1).
    def counting(*args, **kwargs):
        counter.count += 1
        return original(*args, **kwargs)

    with patch.object(model, "from_db", counting):
        yield counter
