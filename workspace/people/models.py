from django.conf import settings
from django.db import models

from workspace.common.uuids import uuid_v7_or_v4

EMAIL_TYPES = ("home", "work", "other")
PHONE_TYPES = ("home", "work", "cell", "fax", "other")
ADDRESS_TYPES = ("home", "work", "other")

SEARCH_TEXT_MAX_LENGTH = 1024


def _one_scope_constraint(name):
    """Exactly one of ``owner`` / ``group`` is set."""
    return models.CheckConstraint(
        condition=(
            models.Q(owner__isnull=False, group__isnull=True)
            | models.Q(owner__isnull=True, group__isnull=False)
        ),
        name=name,
    )


def search_text_for(person):
    """One lowercase string every search runs against, in place of a JSON scan."""
    parts = [
        person.display_name,
        person.given_name,
        person.family_name,
        person.organization,
        *(entry.get("value", "") for entry in person.emails or []),
        *(entry.get("value", "") for entry in person.phones or []),
    ]
    text = " ".join(part.strip().lower() for part in parts if part and part.strip())
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
        ]
        indexes = [
            models.Index(fields=["owner", "display_name"], name="person_owner_name"),
            models.Index(fields=["group", "display_name"], name="person_group_name"),
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
        ]

    def __str__(self):
        return self.name
