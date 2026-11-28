import math

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from workspace.common.qr import QR_IMAGE_KINDS
from workspace.common.serializers import ApiModelSerializer, SummaryField
from workspace.common.services.image import SOURCE_TYPES
from workspace.users.queries import pickable_users
from workspace.users.serializers import GroupSummarySerializer, UserSummarySerializer

from .models import ADDRESS_TYPES, EMAIL_TYPES, PHONE_TYPES, Person, PersonList

User = get_user_model()


def own_groups(queryset, context):
    """The groups whose address book the caller may file a contact in: the
    ones they are in. A group owns its book, so naming it takes being in it."""
    return queryset.filter(pk__in=context["request"].user.groups.values("pk"))


def pickable(queryset, context):
    return queryset.filter(pk__in=pickable_users().values("pk"))


class EmailEntrySerializer(serializers.Serializer):
    # On a PATCH, DRF skips a field missing from the input instead of applying
    # its default (Field.validate_empty_values, partial root) - so a bare
    # `{}` entry would otherwise store with neither `value` nor `type`.
    value = serializers.EmailField(max_length=254)
    type = serializers.ChoiceField(choices=EMAIL_TYPES, default="other")

    def validate(self, attrs):
        if not attrs.get("value"):
            raise serializers.ValidationError(_("A value is required."))
        attrs.setdefault("type", "other")
        return attrs


class PhoneEntrySerializer(serializers.Serializer):
    value = serializers.CharField(max_length=64)
    type = serializers.ChoiceField(choices=PHONE_TYPES, default="other")

    def validate(self, attrs):
        if not attrs.get("value"):
            raise serializers.ValidationError(_("A value is required."))
        attrs.setdefault("type", "other")
        return attrs


class AddressEntrySerializer(serializers.Serializer):
    street = serializers.CharField(max_length=255, allow_blank=True, default="")
    city = serializers.CharField(max_length=255, allow_blank=True, default="")
    region = serializers.CharField(max_length=255, allow_blank=True, default="")
    postal_code = serializers.CharField(max_length=32, allow_blank=True, default="")
    country = serializers.CharField(max_length=255, allow_blank=True, default="")
    type = serializers.ChoiceField(choices=ADDRESS_TYPES, default="home")

    def validate(self, attrs):
        lines = ("street", "city", "region", "postal_code", "country")
        if not any(attrs.get(line) for line in lines):
            raise serializers.ValidationError(_("An address needs at least one line."))
        for line in lines:
            attrs.setdefault(line, "")
        attrs.setdefault("type", "home")
        return attrs


LINKED_USER_TAKEN = gettext_lazy("Another contact is already linked to this account.")

_GROUP_HELP = (
    "The group whose address book holds it; null for the caller's own address book."
)


def book_of(instance, attrs, user):
    """``{"owner": ...}`` or ``{"group": ...}``: the address book a write
    leaves the row in. A null ``group`` is the caller's own book."""
    if "group" in attrs:
        group = attrs["group"]
        return {"group": group} if group is not None else {"owner": user}
    if instance is not None:
        if instance.owner_id is not None:
            return {"owner": instance.owner}
        return {"group": instance.group}
    return {"owner": user}


class PersonSerializer(ApiModelSerializer):
    emails = EmailEntrySerializer(many=True, required=False)
    phones = PhoneEntrySerializer(many=True, required=False)
    addresses = AddressEntrySerializer(many=True, required=False)
    group = SummaryField(
        GroupSummarySerializer,
        queryset=Group.objects.all(),
        narrow=own_groups,
        allow_null=True,
        required=False,
        help_text=_GROUP_HELP,
    )
    linked_user = SummaryField(
        UserSummarySerializer,
        queryset=User.objects.all(),
        narrow=pickable,
        allow_null=True,
        required=False,
        help_text="The workspace account this contact is, when it is one.",
    )
    # vCard properties keyed by property name: a bare model JSONField would
    # take a string or a list just as happily and the round trip would break.
    extra_properties = serializers.DictField(required=False)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = Person
        fields = [
            "uuid",
            "display_name",
            "given_name",
            "family_name",
            "organization",
            "title",
            "birthday",
            "emails",
            "phones",
            "addresses",
            "extra_properties",
            "notes",
            "source",
            "import_uid",
            "group",
            "linked_user",
            "has_avatar",
            "avatar_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "source",
            "import_uid",
            "has_avatar",
            "created_at",
            "updated_at",
        ]
        # DRF derives a validator from the unique constraints that would make
        # `group` and `linked_user` required together. One contact per account
        # and per address book is checked in validate(), and by the
        # constraints themselves when two writes race.
        validators = []

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_avatar_url(self, person):
        """The image of the linked account, or the contact's own; null
        without either."""
        if person.linked_user_id is not None:
            return reverse("user-avatar-retrieve", args=[person.linked_user_id])
        if person.has_avatar:
            return reverse("person-avatar", args=[person.uuid])
        return None

    def validate(self, attrs):
        if "linked_user" in attrs:
            linked = attrs["linked_user"]
        elif self.instance is not None:
            linked = self.instance.linked_user
        else:
            linked = None
        if linked is not None:
            book = book_of(self.instance, attrs, self.context["request"].user)
            clash = Person.objects.filter(linked_user=linked, **book)
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError({"linked_user": [LINKED_USER_TAKEN]})
        return attrs


class PersonListSerializer(ApiModelSerializer):
    group = SummaryField(
        GroupSummarySerializer,
        queryset=Group.objects.all(),
        narrow=own_groups,
        allow_null=True,
        required=False,
        help_text=_GROUP_HELP + " Set when the list is created.",
    )
    member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = PersonList
        fields = ["uuid", "name", "group", "member_count", "created_at"]
        read_only_fields = ["uuid", "created_at"]
        # As for a contact: a name taken in the address book is refused by
        # the constraint, which the views turn into an error on `name`.
        validators = []

    def validate_group(self, group):
        if self.instance is not None and group != self.instance.group:
            raise serializers.ValidationError(
                _("A list cannot change scope; create one in the other address book.")
            )
        return group


class ListDataSerializer(serializers.Serializer):
    """What a batch about a contact list takes: the list."""

    list = serializers.UUIDField()


class PersonPromoteSerializer(serializers.Serializer):
    """A mail correspondent (``email``, ``name``) or a workspace account (``user``)."""

    email = serializers.EmailField(max_length=254, required=False)
    name = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    user = serializers.PrimaryKeyRelatedField(
        source="account",
        queryset=pickable_users(),
        required=False,
    )

    def validate(self, attrs):
        if ("email" in attrs) == ("account" in attrs):
            raise serializers.ValidationError(_("Give either an email or a user."))
        return attrs


class OwnGroupField(serializers.PrimaryKeyRelatedField):
    def get_queryset(self):
        return self.context["request"].user.groups.all()


IMPORT_MAX_SIZE = 20 * 1024 * 1024


class PersonImportSerializer(serializers.Serializer):
    file = serializers.FileField(help_text="A .vcf file, 20 MB at most.")
    group = OwnGroupField(
        queryset=Group.objects.all(),
        required=False,
        allow_null=True,
        help_text=_GROUP_HELP,
    )

    def validate_file(self, upload):
        if upload.size > IMPORT_MAX_SIZE:
            raise serializers.ValidationError(
                _("File too large. Maximum size is 20 MB.")
            )
        return upload


class ImportReportSerializer(serializers.Serializer):
    created = serializers.IntegerField(help_text="Contacts the file added.")
    updated = serializers.IntegerField(
        help_text="Contacts the file brought up to date."
    )
    lists = serializers.IntegerField(help_text="Contact lists the file carried.")


class QRCodeQuerySerializer(serializers.Serializer):
    # `kind`, not `format`: DRF reserves the latter for picking a renderer
    # and answers 404 for a value none of them declares.
    kind = serializers.ChoiceField(
        choices=QR_IMAGE_KINDS, required=False, default="svg"
    )


AVATAR_MAX_SIZE = 10 * 1024 * 1024


class AvatarUploadSerializer(serializers.Serializer):
    """The image, and the square of it to keep, in its own pixels."""

    file = serializers.FileField(
        help_text="A JPEG, PNG, WebP or GIF image, 10 MB at most."
    )
    crop_x = serializers.FloatField(required=False, default=0)
    crop_y = serializers.FloatField(required=False, default=0)
    crop_w = serializers.FloatField()
    crop_h = serializers.FloatField()

    def validate_file(self, image):
        if image.content_type not in SOURCE_TYPES:
            raise serializers.ValidationError(
                _("Unsupported image type. Use JPEG, PNG, WebP, or GIF.")
            )
        if image.size > AVATAR_MAX_SIZE:
            raise serializers.ValidationError(
                _("Image too large. Maximum size is 10 MB.")
            )
        return image

    def validate(self, attrs):
        # Each side is named for itself: a client that sent one bad number
        # learns which.
        refused = {}
        for key in ("crop_x", "crop_y", "crop_w", "crop_h"):
            positive = attrs[key] > 0 or key in ("crop_x", "crop_y")
            if not math.isfinite(attrs[key]) or not positive:
                refused[key] = [_("Invalid crop coordinates.")]
        if refused:
            raise serializers.ValidationError(refused)
        return attrs
