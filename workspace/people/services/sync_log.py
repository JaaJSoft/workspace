"""Change tracking behind CardDAV sync tokens and ETags.

Every write to a person or a list bumps the revision of its address book and
stamps the card with it in ``AddressBookChange``. A sync token is a past
revision: what changed since is every row stamped after it. A card's ETag is
derived from its own stamp, so it moves exactly when the card does.

The hooks are model signals, as for calendars: contacts are written from the
web UI, the REST API, vCard imports, account promotion and CardDAV itself,
and a writer that forgot to call in would leave phones showing stale cards.
The flip side: a queryset ``update()`` sends no signal, so it must not touch
a field a card renders.

A list card names its members, so it changes with its membership and with a
member's UID; both are tracked here too.
"""

from django.db.models import F, QuerySet
from django.db.models.signals import (
    m2m_changed,
    post_delete,
    post_init,
    post_save,
    pre_delete,
)
from django.dispatch import receiver

from ..models import AddressBook, AddressBookChange, Person, PersonList

# Part of every ETag: bump it when the rendering of a card changes, so
# clients refetch cards whose rows did not move.
RENDER_VERSION = 1


def object_name(card):
    """The CardDAV resource name of a person or a list."""
    return card.dav_name or f"{card.uuid}.vcf"


def object_etag(object_uuid, revision):
    return f'"{object_uuid.hex[:12]}-{revision}-{RENDER_VERSION}"'


def _book_filter(owner_id, group_id):
    return {"owner_id": owner_id} if owner_id is not None else {"group_id": group_id}


def book_revision(owner_id, group_id):
    """The address book's current revision; 0 until anything in it changed."""
    revision = (
        AddressBook.objects.filter(**_book_filter(owner_id, group_id))
        .values_list("sync_revision", flat=True)
        .first()
    )
    return revision or 0


def object_revisions(owner_id, group_id, object_uuids=None):
    """``{object_uuid: revision}`` for the cards of a book that have one.

    A card with no row has not changed since change tracking began, and is
    at revision 0.
    """
    rows = AddressBookChange.objects.filter(
        **{f"address_book__{k}": v for k, v in _book_filter(owner_id, group_id).items()}
    )
    if object_uuids is not None:
        rows = rows.filter(object_uuid__in=object_uuids)
    return dict(rows.values_list("object_uuid", "revision"))


def changes_since(owner_id, group_id, revision):
    """The change rows stamped after *revision*, oldest first."""
    return AddressBookChange.objects.filter(
        **{
            f"address_book__{k}": v for k, v in _book_filter(owner_id, group_id).items()
        },
        revision__gt=revision,
    ).order_by("revision")


def record_change(owner_id, group_id, object_uuid, name):
    """Stamp one card of the address book with a fresh revision; return it."""
    book, _created = AddressBook.objects.get_or_create(
        **_book_filter(owner_id, group_id)
    )
    AddressBook.objects.filter(pk=book.pk).update(sync_revision=F("sync_revision") + 1)
    revision = (
        AddressBook.objects.filter(pk=book.pk)
        .values_list("sync_revision", flat=True)
        .get()
    )
    AddressBookChange.objects.update_or_create(
        address_book=book,
        object_uuid=object_uuid,
        defaults={"name": name, "revision": revision},
    )
    return revision


def _record(card):
    record_change(card.owner_id, card.group_id, card.uuid, object_name(card))


def _record_lists(list_ids):
    for person_list in PersonList.objects.filter(pk__in=list_ids):
        _record(person_list)


def _deleted_on_its_own(origin, model):
    """True when the deletion started from *model* rows, not from their scope.

    Deleting a user or a group cascades to its contacts and to its
    ``AddressBook`` row; stamping a book that is about to disappear would
    recreate it, and fail on the foreign key at commit.
    """
    if isinstance(origin, model):
        return True
    return isinstance(origin, QuerySet) and origin.model is model


# Read from __dict__: a deferred field must not cost a query here.
def _loaded(instance, name):
    return instance.__dict__.get(name)


@receiver(post_init, sender=Person)
def _remember_person(sender, instance, **kwargs):
    instance._sync_scope = (
        _loaded(instance, "owner_id"),
        _loaded(instance, "group_id"),
    )
    instance._sync_uid = _loaded(instance, "import_uid")


@receiver(post_save, sender=Person)
def _on_person_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    previous_scope = getattr(instance, "_sync_scope", (None, None))
    scope = (instance.owner_id, instance.group_id)
    if not created and any(previous_scope) and previous_scope != scope:
        # Moved: the old book must report the card as gone.
        record_change(*previous_scope, instance.uuid, object_name(instance))
    _record(instance)
    previous_uid = getattr(instance, "_sync_uid", None)
    if not created and previous_uid is not None and previous_uid != instance.import_uid:
        _record_lists(instance.lists.values_list("pk", flat=True))
    instance._sync_scope = scope
    instance._sync_uid = instance.import_uid


@receiver(pre_delete, sender=Person)
def _collect_person_lists(sender, instance, origin=None, **kwargs):
    if _deleted_on_its_own(origin, Person):
        # Read now: the membership rows go before the person does.
        instance._sync_list_ids = list(instance.lists.values_list("pk", flat=True))


@receiver(post_delete, sender=Person)
def _on_person_deleted(sender, instance, origin=None, **kwargs):
    if not _deleted_on_its_own(origin, Person):
        return
    _record(instance)
    _record_lists(getattr(instance, "_sync_list_ids", []))


@receiver(post_save, sender=PersonList)
def _on_list_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _record(instance)


@receiver(post_delete, sender=PersonList)
def _on_list_deleted(sender, instance, origin=None, **kwargs):
    if _deleted_on_its_own(origin, PersonList):
        _record(instance)


@receiver(m2m_changed, sender=PersonList.members.through)
def _on_membership_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action == "pre_clear" and reverse:
        # A person's lists, cleared: which ones is unknowable afterwards.
        instance._sync_cleared_list_ids = list(
            instance.lists.values_list("pk", flat=True)
        )
        return
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if action != "post_clear" and not pk_set:
        # Adding members already there, or removing absent ones: no change.
        return
    if not reverse:
        _record(instance)
    elif action == "post_clear":
        _record_lists(getattr(instance, "_sync_cleared_list_ids", []))
    else:
        _record_lists(pk_set or [])
