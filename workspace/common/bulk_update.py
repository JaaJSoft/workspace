"""``bulk_update`` at a cost that follows what it writes.

Django writes a ``bulk_update`` as one ``CASE`` per field with a ``WHEN`` per
object, so a statement costs the square of its batch, and it makes the batch
as wide as the database allows. ``QuerySet.bulk_update`` is wrapped once here,
and every caller keeps writing ``bulk_update``.

Plain values go to the database as a table of them the rows are joined on,
``UPDATE ... FROM (VALUES ...)``, through django-fast-update: one pass over
the rows whatever their number. 12000 rows on three fields take 0.2 s instead
of 3.5 s on SQLite, 0.6 s instead of 4.2 s on PostgreSQL.

What that form cannot express stays with Django: a value that is an
expression, an object listed twice, a model spread over several tables. There
the batches are sized here, because Django sizes them wrong twice:

- as wide as it can, which on PostgreSQL is every object at once. 12000
  objects on three fields took four minutes in one statement;
- as if each object bound one parameter per field plus two for its key, where
  it binds two per field plus one. With three fields or more a full batch
  overran SQLite's limit, ``too many SQL variables`` from 6553 objects up.
"""

from functools import wraps

from django.core.exceptions import FieldDoesNotExist
from django.db import connections
from django.db.models import QuerySet
from fast_update.fast import fast_update
from fast_update.query import sanity_check

# Django's own form is flat from 50 to 2000 objects on both databases, far
# below where the square starts to show.
DEFAULT_BATCH = 500


def widest_batch(model, fields, connection):
    """How many objects one statement of Django's form can carry, None when
    the database sets no limit.

    Counted for plain values. An expression binds what it is made of, which
    the default batch leaves room for many times over.
    """
    limit = connection.features.max_query_params
    if not limit:
        return None
    key_columns = len(model._meta.pk_fields)
    per_object = len(fields) * (key_columns + 1) + key_columns
    return max(limit // per_object, 1)


def _fits_a_values_table(model, objs, names, batch_size):
    """Whether the call is one django-fast-update takes as Django would.

    Anything else is Django's to carry out, or to refuse in its own words.
    """
    if not objs or not names or (batch_size is not None and batch_size <= 0):
        return False
    opts = model._meta
    if opts.parents or len(opts.pk_fields) != 1 or len(set(names)) != len(names):
        return False
    try:
        fields = [opts.get_field(name) for name in names]
    except FieldDoesNotExist:
        return False
    if any(
        not field.concrete or field.many_to_many or field.primary_key or field.generated
        for field in fields
    ):
        return False
    keys = {obj.pk for obj in objs}
    if None in keys or len(keys) != len(objs):
        return False
    attnames = [field.attname for field in fields]
    return not any(
        hasattr(getattr(obj, attname), "resolve_expression")
        for obj in objs
        for attname in attnames
    )


def bind_bulk_update():
    django_bulk_update = QuerySet.bulk_update
    if getattr(django_bulk_update, "is_bound_here", False):
        return

    @wraps(django_bulk_update)
    def bulk_update(self, objs, fields, batch_size=None):
        objs, fields = tuple(objs), list(fields)
        if _fits_a_values_table(self.model, objs, fields, batch_size):
            sanity_check(self.model, objs, fields, "bulk_update", batch_size)
            return fast_update(self, objs, fields, batch_size)
        if batch_size is None:
            batch_size = DEFAULT_BATCH
        widest = widest_batch(self.model, fields, connections[self.db])
        if widest is not None and batch_size > widest:
            batch_size = widest
        return django_bulk_update(self, objs, fields, batch_size=batch_size)

    bulk_update.is_bound_here = True
    QuerySet.bulk_update = bulk_update
