"""``bulk_update`` batches of a size the database handles well.

Django leaves a ``bulk_update`` batch as wide as the database allows, and that
is wrong twice:

- It writes one ``WHEN`` per object and per field, so a statement costs the
  square of its batch. PostgreSQL sets no limit and gets every object at once:
  12000 objects on three fields took four minutes there, four seconds in
  batches.
- It sizes a batch as if each object bound one parameter per field plus two
  for its key, where it binds two per field, a key and a value in every
  ``WHEN``, plus one key in the filter. With three fields or more a full
  batch overran SQLite's limit: ``too many SQL variables`` from 6553 objects
  up for three fields, 4680 for five.

``bulk_update`` is wrapped once, on ``QuerySet``. A call that names no
``batch_size`` gets ``DEFAULT_BATCH``, and no batch is wider than what the
statement really binds. A caller's own ``batch_size`` is kept while it fits.
"""

from functools import wraps

from django.db import connections
from django.db.models import QuerySet

# Flat from 50 to 2000 objects on both databases, far below where the square
# starts to show.
DEFAULT_BATCH = 500


def widest_batch(model, fields, connection):
    """How many objects one ``bulk_update`` statement can carry, None when
    the database sets no limit."""
    limit = connection.features.max_query_params
    if not limit:
        return None
    key_columns = len(model._meta.pk_fields)
    per_object = len(fields) * (key_columns + 1) + key_columns
    return max(limit // per_object, 1)


def bind_bulk_update_batches():
    django_bulk_update = QuerySet.bulk_update
    if getattr(django_bulk_update, "sizes_batches", False):
        return

    @wraps(django_bulk_update)
    def bulk_update(self, objs, fields, batch_size=None):
        fields = list(fields)
        if batch_size is None:
            batch_size = DEFAULT_BATCH
        widest = widest_batch(self.model, fields, connections[self.db])
        if widest is not None and batch_size > widest:
            batch_size = widest
        return django_bulk_update(self, objs, fields, batch_size=batch_size)

    bulk_update.sizes_batches = True
    QuerySet.bulk_update = bulk_update
