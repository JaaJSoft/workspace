"""Stable integer keys for SQLite virtual tables indexing a UUID-keyed table.

FTS5 and vec0 tables are keyed on a 64-bit integer rowid. It cannot be the
base table's implicit rowid: Django's SQLite schema editor rebuilds a table
for many operations (create-copy-drop-rename; any AddField reaches it) and
SQLite reassigns implicit rowids on the copy, so an index keyed on them keeps
answering - with the wrong rows. The key lives in an ordinary unique integer
column instead, copied like any other value, and is claimed here.
"""

from django.db import IntegrityError, transaction

from workspace.common.uuids import parse_uuid_or_none

# Virtual-table rowids are signed 64-bit, so the key is the low 63 bits of the
# UUID.
ROWID_MASK = (1 << 63) - 1
ROWID_ATTEMPTS = 4


def claim_rowid(cursor, *, read_sql, assign_sql, pk, param):
    """Give the row a stable key, once. True when it has one.

    *read_sql* selects the key column for one pk, *assign_sql* sets it only
    where it is still NULL; both bind the pk as *param*.

    The key is derived from the UUID rather than taken from a sequence:
    deterministic, needs no coordination between concurrent indexing tasks,
    and stays put when Django copies the table. Uniqueness is still the
    database's call - the column carries a unique constraint - so a collision
    (about 62 bits of entropy) surfaces as an IntegrityError rather than two
    rows quietly sharing one index entry, and the next candidate is tried.
    """
    cursor.execute(read_sql, [param])
    row = cursor.fetchone()
    if row is None:
        return False  # the row is gone; nothing to index
    if row[0] is not None:
        return True

    parsed = parse_uuid_or_none(pk)
    if parsed is None:
        return False
    for attempt in range(ROWID_ATTEMPTS):
        candidate = (parsed.int + attempt) & ROWID_MASK
        try:
            with transaction.atomic(using=cursor.db.alias):
                cursor.execute(assign_sql, [candidate, param])
        except IntegrityError:
            continue
        return True
    return False


def adapt_pk(pk, conn):
    """Bind a UUID the way the backend stores it (char(32) hex on SQLite)."""
    parsed = parse_uuid_or_none(pk)
    if parsed is None:
        return pk
    return parsed if conn.features.has_native_uuid_field else parsed.hex
