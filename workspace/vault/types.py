"""Entry types, as Python proxies over one flat table.

Adding a type is a class and a schema: no migration, no table, no template.
What a type may *declare* is bounded by the closed field catalogue, and the
check runs at class definition time - a schema that named ``name`` would derive
the associated data of ``VaultEntry.encrypted_name``, and the two ciphertexts
would become interchangeable.
"""

from dataclasses import dataclass

from django.db import models

from .models import EntryType, VaultEntry
from .services.fields import RESERVED_FIELD_IDS

_REGISTRY: dict[str, type[VaultEntry]] = {}


@dataclass(frozen=True)
class Field:
    """One field an entry type carries.

    ``field_id`` is what enters the associated data; everything else drives
    the form and the properties panel and is free to change.
    """

    field_id: str
    label: str
    secret: bool = False
    generator: bool = False
    kind: str = "text"


class TypedEntryManager(models.Manager):
    """Narrows every queryset to one entry type, both ways: reads are filtered
    and creates carry the type without the caller passing it."""

    def __init__(self, entry_type):
        super().__init__()
        self.entry_type = entry_type

    def get_queryset(self):
        return super().get_queryset().filter(type=self.entry_type)

    def create(self, **kwargs):
        # setdefault alone would let LoginEntry.objects.create(type="passport")
        # through, and as_typed could then not re-cast the row it just wrote.
        declared = kwargs.setdefault("type", self.entry_type)
        if declared != self.entry_type:
            raise ValueError(
                f"{self.model.__name__} cannot create a {declared!r} entry"
            )
        return super().create(**kwargs)


class TypedEntry(VaultEntry):
    """Base of every entry proxy. Not registered itself."""

    ENTRY_TYPE = None
    FIELD_SCHEMA = ()

    class Meta:
        proxy = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for field in cls.FIELD_SCHEMA:
            if field.field_id not in RESERVED_FIELD_IDS:
                raise ValueError(
                    f"{cls.__name__} declares {field.field_id!r}, which is not a "
                    "reserved field identifier"
                )
        if cls.ENTRY_TYPE is not None:
            _REGISTRY[cls.ENTRY_TYPE] = cls


class LoginEntry(TypedEntry):
    ENTRY_TYPE = EntryType.LOGIN
    objects = TypedEntryManager(EntryType.LOGIN)

    class Meta:
        proxy = True

    FIELD_SCHEMA = (
        Field("username", label="Username"),
        Field("password", label="Password", secret=True, generator=True),
        Field("totp", label="Authenticator key", secret=True, kind="totp"),
        Field("uri", label="Website"),
    )


def registry_for(entry_type: str) -> type[VaultEntry]:
    """The proxy class for *entry_type*, or ``KeyError``."""
    return _REGISTRY[entry_type]


def schema_for(entry_type: str):
    return registry_for(entry_type).FIELD_SCHEMA


def as_typed(entry: VaultEntry) -> VaultEntry:
    """Re-cast a row as its type's proxy.

    Django proxies share the concrete model's table and fields, so this is a
    re-labelling of the same row, not a query.
    """
    proxy = registry_for(entry.type)
    typed = proxy(
        **{
            field.attname: getattr(entry, field.attname)
            for field in entry._meta.concrete_fields
        }
    )
    # Without these a re-cast row believes it is unsaved, and a later save()
    # issues an INSERT that collides on the primary key.
    typed._state.adding = entry._state.adding
    typed._state.db = entry._state.db
    return typed
