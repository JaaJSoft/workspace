"""``bulk_update`` batches that fit what the database binds in one statement.

Django sizes a ``bulk_update`` batch as if each object bound one parameter per
field plus two for its key. It binds two per field, a key and a value in every
``WHEN``, plus one key in the filter: with two fields or more, a batch
overruns the limit it was sized for. On SQLite that is ``too many SQL
variables`` from 6553 objects up for three fields, 4680 for five.

``bulk_update`` is wrapped once, on ``QuerySet``, to cap the batch at what the
statement really binds. A caller's own smaller ``batch_size`` is kept, and a
database without a limit is left alone.
"""

from functools import wraps

from django.db import connections
from django.db.models import QuerySet


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
    if getattr(django_bulk_update, "caps_batches", False):
        return

    @wraps(django_bulk_update)
    def bulk_update(self, objs, fields, batch_size=None):
        fields = list(fields)
        widest = widest_batch(self.model, fields, connections[self.db])
        if widest is not None and (batch_size is None or batch_size > widest):
            batch_size = widest
        return django_bulk_update(self, objs, fields, batch_size=batch_size)

    bulk_update.caps_batches = True
    QuerySet.bulk_update = bulk_update
