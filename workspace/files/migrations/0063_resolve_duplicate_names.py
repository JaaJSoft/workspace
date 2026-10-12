"""Rename the live nodes that share a name with a sibling.

The next migration makes a live name unique per folder, case-insensitively,
and cannot be applied while two siblings hold one. Nothing enforced that
before but an application check, which a race walks past - and which a
case-sensitive disk scan or a restored folder never ran - so an install may
hold a few.

The oldest node of each clash keeps its name; the others become
``name (Copy N)``, the variant every "keep both" flow picks. A renamed node's
bytes follow it into the path its new name derives from. Two rows can point
at one blob - an exact duplicate written by the race, or two case variants
on a case-insensitive disk - so a blob another row still reads is copied
rather than moved.

Not atomic: a blob that moved cannot roll back with its row, so each row is
written as soon as its bytes are where it says.
"""

import logging
import os
import posixpath

from django.db import migrations, transaction
from django.db.models import Count
from django.db.models.functions import Lower

logger = logging.getLogger(__name__)


def scrub(value):
    """Inlined from ``common.logging``: a migration does not import live code."""
    return str(value).replace("\r", "").replace("\n", "")


# The scopes of the three constraints the next migration adds.
SCOPES = (
    ({"parent__isnull": False, "group__isnull": False}, ("parent_id", "group_id")),
    ({"parent__isnull": False, "group__isnull": True}, ("parent_id", "owner_id")),
    ({"parent__isnull": True, "group__isnull": True}, ("owner_id",)),
)


def resolve_duplicate_names(apps, schema_editor):
    File = apps.get_model("files", "File")
    db = schema_editor.connection.alias
    storage = File._meta.get_field("content").storage

    for condition, keys in SCOPES:
        live = File.objects.using(db).filter(deleted_at__isnull=True, **condition)
        clashes = (
            live.annotate(lname=Lower("name"))
            .values(*keys, "lname")
            .annotate(n=Count("pk"))
            .filter(n__gt=1)
            .order_by()
        )
        for clash in list(clashes):
            scope = {key: clash[key] for key in keys}
            siblings = live.filter(**scope)
            holders = list(
                siblings.annotate(lname=Lower("name"))
                .filter(lname=clash["lname"])
                .select_related("owner")
                .order_by("created_at", "pk")
            )
            taken = {
                name.casefold() for name in siblings.values_list("name", flat=True)
            }
            keeper = holders[0]
            for row in holders[1:]:
                new_name = _copy_name(row.name, row.node_type, taken)
                taken.add(new_name.casefold())
                _rename(File, db, storage, row, new_name, keeper)


def _copy_name(name, node_type, taken):
    counter = 1
    while True:
        suffix = "Copy" if counter == 1 else f"Copy {counter}"
        parts = name.rsplit(".", 1)
        if len(parts) == 2 and node_type == "file":
            candidate = f"{parts[0]} ({suffix}).{parts[1]}"
        else:
            candidate = f"{name} ({suffix})"
        if candidate.casefold() not in taken:
            return candidate
        counter += 1


def _live_dir(row):
    """The live directory *row*'s path stands for, ``row.path`` included."""
    if row.group_id:
        return posixpath.join("files", "groups", *row.path.split("/"))
    return posixpath.join("files", "users", row.owner.username, *row.path.split("/"))


def _parent_dir(File, db, row):
    if row.parent_id:
        parent = File.objects.using(db).select_related("owner").get(pk=row.parent_id)
        return _live_dir(parent)
    if row.group_id:
        return posixpath.join("files", "groups")
    return posixpath.join("files", "users", row.owner.username)


def _rename(File, db, storage, row, new_name, keeper):
    old_path = row.path or row.name
    new_path = posixpath.join(posixpath.dirname(old_path), new_name)
    parent_dir = _parent_dir(File, db, row)
    destination = posixpath.join(parent_dir, new_name)
    logger.info("Renaming duplicate %s to %s", scrub(old_path), scrub(new_name))

    if row.node_type == "file":
        content = _relocate(File, db, storage, row, destination, exclude={row.pk})
        File.objects.using(db).filter(pk=row.pk).update(
            name=new_name, path=new_path, content=content
        )
        return

    source_dir = _live_dir(row)
    subtree = _subtree(File, db, row)
    moved = {row.pk} | {node.pk for node in subtree}
    # The tree first, in one go: until every path follows, the subtree reads
    # as half renamed. Each blob then moves with its own row update, so a
    # failure partway leaves every row pointing where its bytes are.
    with transaction.atomic(using=db):
        File.objects.using(db).filter(pk=row.pk).update(name=new_name, path=new_path)
        for node in subtree:
            File.objects.using(db).filter(pk=node.pk).update(
                path=new_path + node.path[len(old_path) :]
            )
    _makedirs(storage, destination)
    for node in subtree:
        content = (node.content.name or "").replace("\\", "/")
        if node.node_type == "folder":
            _makedirs(storage, destination + node.path[len(old_path) :])
        elif content.startswith(f"{source_dir}/"):
            # Trashed descendants sit under trash/ and stay where they are.
            node_destination = destination + content[len(source_dir) :]
            File.objects.using(db).filter(pk=node.pk).update(
                content=_relocate(
                    File, db, storage, node, node_destination, exclude=moved
                )
            )
    # An exact duplicate shares the keeper's directory, and so does a case
    # variant on a case-insensitive disk: its empty folders are the keeper's.
    if not _same_place(storage, source_dir, posixpath.join(parent_dir, keeper.name)):
        _prune_empty_dirs(storage, source_dir)


def _subtree(File, db, root):
    """Every node under *root*, found by identity.

    An exact duplicate folder shares its path with the one it duplicates, so
    a path prefix would sweep up the other folder's children too.
    """
    found = []
    level = [root.pk]
    while level:
        children = list(File.objects.using(db).filter(parent_id__in=level))
        found.extend(children)
        level = [child.pk for child in children if child.node_type == "folder"]
    return found


def _relocate(File, db, storage, row, destination, exclude):
    """Put *row*'s bytes at *destination*; return the path the row should hold."""
    source = (row.content.name or "").replace("\\", "/")
    if not source:
        return row.content.name
    # Case-insensitively: on a case-insensitive disk two case variants are one
    # file. Copying a blob that turns out to be the row's own costs a copy.
    shared = (
        File.objects.using(db)
        .exclude(pk__in=exclude)
        .filter(content__iexact=source)
        .exists()
    )
    try:
        if not storage.exists(source):
            logger.warning("Duplicate's blob already missing: %s", scrub(source))
            return destination
        try:
            source_full = storage.path(source)
        except NotImplementedError:
            source_full = None
        if source_full is not None and os.path.isdir(source_full):
            # A stale file row pointing at what is now a directory: moving it
            # would carry off content the row never owned.
            logger.warning("Not moving %s: it is a directory", scrub(source))
            return source
        if shared:
            with storage.open(source, "rb") as fh:
                return storage.save(destination, fh)
        if source_full is None:
            with storage.open(source, "rb") as fh:
                saved = storage.save(destination, fh)
            storage.delete(source)
            return saved
        destination_full = storage.path(destination)
        os.makedirs(os.path.dirname(destination_full), exist_ok=True)
        os.replace(source_full, destination_full)
        return destination
    except OSError as e:
        logger.error("Could not move duplicate %s: %s", scrub(source), scrub(e))
        return source


def _same_place(storage, path, other):
    try:
        return os.path.samefile(storage.path(path), storage.path(other))
    except NotImplementedError, OSError:
        return False


def _makedirs(storage, dir_path):
    try:
        os.makedirs(storage.path(dir_path), exist_ok=True)
    except NotImplementedError:
        pass  # a storage without local paths has no directories to create


def _prune_empty_dirs(storage, dir_path):
    """Drop the directories the renamed folder left empty, if any."""
    try:
        full = storage.path(dir_path)
    except NotImplementedError:
        return
    if not os.path.isdir(full):
        return
    for current, _subdirs, _names in os.walk(full, topdown=False):
        try:
            os.rmdir(current)
        except OSError:
            pass  # still holds the kept folder's content


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("files", "0062_processing_pipeline"),
    ]

    operations = [
        migrations.RunPython(resolve_duplicate_names, migrations.RunPython.noop),
    ]
