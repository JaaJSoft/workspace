from django.conf import settings
from django.db import models

from workspace.common.text import fold_text
from workspace.common.uuids import uuid_v7_or_v4

EMAIL_TYPES = ("home", "work", "other")
PHONE_TYPES = ("home", "work", "cell", "fax", "other")
ADDRESS_TYPES = ("home", "work", "other")

SEARCH_TEXT_MAX_LENGTH = 1024

SOURCE_MANUAL = "manual"
SOURCE_IMPORT = "import"
SOURCE_CARDDAV = "carddav"
SOURCE_CHOICES = [
    (SOURCE_MANUAL, "Manual"),
    (SOURCE_IMPORT, "Import"),
    (SOURCE_CARDDAV, "Synced from a device"),
]


def _one_scope_constraint(name):
    """Exactly one of ``owner`` / ``group`` is set."""
    return models.CheckConstraint(
        condition=(
            models.Q(owner__isnull=False, group__isnull=True)
            | models.Q(owner__isnull=True, group__isnull=False)
        ),
        name=name,
    )


def _unique_dav_name_constraints(prefix):
    """A CardDAV resource name is unique within its address book."""
    return [
        models.UniqueConstraint(
            fields=[scope, "dav_name"],
            condition=models.Q(**{f"{scope}__isnull": False}) & ~models.Q(dav_name=""),
            name=f"{prefix}_unique_dav_name_per_{scope}",
        )
        for scope in ("owner", "group")
    ]


def search_text_for(person):
    """One folded string every search runs against, in place of a JSON scan.

    Folded with ``fold_text``: a search must fold its query the same way, or
    an accented or capitalized query matches nothing.
    """
    parts = [
        person.display_name,
        person.given_name,
        person.family_name,
        person.organization,
        *(entry.get("value", "") for entry in person.emails or []),
        *(entry.get("value", "") for entry in person.phones or []),
    ]
    text = fold_text(" ".join(part.strip() for part in parts if part and part.strip()))
    return text[:SEARCH_TEXT_MAX_LENGTH]


class Person(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="persons",
    )
    group = models.ForeignKey(
        "auth.Group",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="persons",
    )
    display_name = models.CharField(max_length=255)
    given_name = models.CharField(max_length=255, blank=True, default="")
    family_name = models.CharField(max_length=255, blank=True, default="")
    organization = models.CharField(max_length=255, blank=True, default="")
    title = models.CharField(max_length=255, blank=True, default="")
    birthday = models.DateField(null=True, blank=True)
    emails = models.JSONField(default=list, blank=True)
    phones = models.JSONField(default=list, blank=True)
    addresses = models.JSONField(default=list, blank=True)
    # vCard properties with no column of their own, kept for the round trip.
    extra_properties = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")
    source = models.CharField(
        max_length=16, choices=SOURCE_CHOICES, default=SOURCE_MANUAL
    )
    # The vCard UID the person came in with: a re-import finds it again by
    # this, before falling back to an email match.
    import_uid = models.CharField(max_length=255, blank=True, default="")
    # CardDAV resource name in its address book; blank means "<uuid>.vcf".
    dav_name = models.CharField(max_length=255, blank=True, default="")
    linked_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="linked_persons",
    )
    has_avatar = models.BooleanField(default=False)
    search_text = models.CharField(
        max_length=SEARCH_TEXT_MAX_LENGTH, blank=True, default="", db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name", "uuid"]
        constraints = [
            _one_scope_constraint("person_one_scope"),
            models.UniqueConstraint(
                fields=["owner", "linked_user"],
                condition=models.Q(owner__isnull=False, linked_user__isnull=False),
                name="person_unique_linked_user_per_owner",
            ),
            models.UniqueConstraint(
                fields=["group", "linked_user"],
                condition=models.Q(group__isnull=False, linked_user__isnull=False),
                name="person_unique_linked_user_per_group",
            ),
            *_unique_dav_name_constraints("person"),
        ]
        indexes = [
            models.Index(fields=["owner", "display_name"], name="person_owner_name"),
            models.Index(fields=["group", "display_name"], name="person_group_name"),
            models.Index(fields=["owner", "import_uid"], name="person_owner_uid"),
            models.Index(fields=["group", "import_uid"], name="person_group_uid"),
        ]

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        self.search_text = search_text_for(self)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "search_text" not in update_fields:
            kwargs["update_fields"] = [*update_fields, "search_text"]
        super().save(*args, **kwargs)

    @property
    def primary_email(self):
        return self.emails[0]["value"] if self.emails else ""


class PersonList(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="person_lists",
    )
    group = models.ForeignKey(
        "auth.Group",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="person_lists",
    )
    name = models.CharField(max_length=255)
    members = models.ManyToManyField(Person, related_name="lists", blank=True)
    # The UID and CardDAV resource name a device created the list with.
    import_uid = models.CharField(max_length=255, blank=True, default="")
    dav_name = models.CharField(max_length=255, blank=True, default="")
    # Member UIDs a device sent before their cards: served back as sent, and
    # turned into members when the cards arrive.
    pending_member_uids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "uuid"]
        constraints = [
            _one_scope_constraint("person_list_one_scope"),
            models.UniqueConstraint(
                fields=["owner", "name"],
                condition=models.Q(owner__isnull=False),
                name="person_list_unique_name_per_owner",
            ),
            models.UniqueConstraint(
                fields=["group", "name"],
                condition=models.Q(group__isnull=False),
                name="person_list_unique_name_per_group",
            ),
            *_unique_dav_name_constraints("person_list"),
        ]

    def __str__(self):
        return self.name


class AddressBook(models.Model):
    """The sync state of one address book: a user's own, or a group's.

    The address book itself is the scope (``owner`` or ``group``); this row
    only exists once something in it changed, and holds the revision every
    change bumps - the CardDAV sync token and ctag.
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="address_book",
    )
    group = models.OneToOneField(
        "auth.Group",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="address_book",
    )
    sync_revision = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [_one_scope_constraint("address_book_one_scope")]

    def __str__(self):
        return f"{self.owner_id or self.group_id}@{self.sync_revision}"


class AddressBookChange(models.Model):
    """The last revision at which one card of an address book changed.

    A card is a person or a list. One row per card, rewritten on every
    change. The card may be gone - deleted or moved to another book - which
    is why ``object_uuid`` is no foreign key and ``name`` keeps the href to
    report it under.
    """

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    address_book = models.ForeignKey(
        AddressBook, on_delete=models.CASCADE, related_name="changes"
    )
    object_uuid = models.UUIDField()
    name = models.CharField(max_length=255)
    revision = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["address_book", "object_uuid"],
                name="unique_change_per_address_book",
            ),
        ]
        indexes = [
            models.Index(fields=["address_book", "revision"], name="abchange_book_rev"),
        ]

    def __str__(self):
        return f"{self.address_book_id}/{self.name}@{self.revision}"
