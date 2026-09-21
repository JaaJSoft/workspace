from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import ADDRESS_TYPES, EMAIL_TYPES, PHONE_TYPES, Person, PersonList
from .services.persons import create_person, move_to_scope, update_person

User = get_user_model()

SCOPE_MINE = "mine"
SCOPE_GROUP_PREFIX = "group:"


def scope_label(obj):
    """The wire form of a scope: ``mine`` or ``group:<id>``."""
    if obj.owner_id is not None:
        return SCOPE_MINE
    return f"{SCOPE_GROUP_PREFIX}{obj.group_id}"


def parse_scope(user, raw):
    """Turn a wire scope into ``{"owner": user}`` or ``{"group": group}``.

    Raises ``serializers.ValidationError`` on an unknown form or a group the
    user is not a member of - the two look the same from outside on purpose.
    """
    if raw == SCOPE_MINE:
        return {"owner": user}
    if isinstance(raw, str) and raw.startswith(SCOPE_GROUP_PREFIX):
        group_id = raw[len(SCOPE_GROUP_PREFIX) :]
        if group_id.isdigit():
            group = user.groups.filter(pk=int(group_id)).first()
            if group is not None:
                return {"group": group}
    raise serializers.ValidationError("Unknown scope.")


class EmailEntrySerializer(serializers.Serializer):
    # On a PATCH, DRF skips a field missing from the input instead of applying
    # its default (Field.validate_empty_values, partial root) - so a bare
    # `{}` entry would otherwise store with neither `value` nor `type`.
    value = serializers.EmailField(max_length=254)
    type = serializers.ChoiceField(choices=EMAIL_TYPES, default="other")

    def validate(self, attrs):
        if not attrs.get("value"):
            raise serializers.ValidationError("A value is required.")
        attrs.setdefault("type", "other")
        return attrs


class PhoneEntrySerializer(serializers.Serializer):
    value = serializers.CharField(max_length=64)
    type = serializers.ChoiceField(choices=PHONE_TYPES, default="other")

    def validate(self, attrs):
        if not attrs.get("value"):
            raise serializers.ValidationError("A value is required.")
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
            raise serializers.ValidationError("An address needs at least one line.")
        for line in lines:
            attrs.setdefault(line, "")
        attrs.setdefault("type", "home")
        return attrs


class LinkedUserSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="pk")
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    avatar_url = serializers.SerializerMethodField()

    @extend_schema_field(OpenApiTypes.STR)
    def get_avatar_url(self, obj):
        return f"/api/v1/users/{obj.pk}/avatar"


def _linked_user_clash():
    return serializers.ValidationError(
        {"linked_user_id": ["Another contact is already linked to this account."]}
    )


class PersonSerializer(serializers.ModelSerializer):
    emails = EmailEntrySerializer(many=True, required=False)
    phones = PhoneEntrySerializer(many=True, required=False)
    addresses = AddressEntrySerializer(many=True, required=False)
    linked_user = LinkedUserSerializer(read_only=True)
    linked_user_id = serializers.PrimaryKeyRelatedField(
        source="linked_user",
        queryset=User.objects.filter(is_active=True),
        allow_null=True,
        required=False,
        write_only=True,
    )
    scope = serializers.CharField(required=False)
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
            "linked_user",
            "linked_user_id",
            "scope",
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

    @extend_schema_field(OpenApiTypes.STR)
    def get_avatar_url(self, obj):
        if obj.linked_user_id is not None:
            return f"/api/v1/users/{obj.linked_user_id}/avatar"
        if obj.has_avatar:
            return f"/api/v1/people/{obj.uuid}/avatar"
        return None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["scope"] = scope_label(instance)
        return data

    def validate_scope(self, value):
        return parse_scope(self.context["request"].user, value)

    def _current_scope(self, attrs):
        if "scope" in attrs:
            return attrs["scope"]
        if self.instance is not None:
            if self.instance.owner_id is not None:
                return {"owner": self.instance.owner}
            return {"group": self.instance.group}
        return {"owner": self.context["request"].user}

    def validate(self, attrs):
        if "linked_user" in attrs:
            linked = attrs["linked_user"]
        elif self.instance is not None:
            linked = self.instance.linked_user
        else:
            linked = None
        if linked is not None:
            clash = Person.objects.filter(
                linked_user=linked, **self._current_scope(attrs)
            )
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {
                        "linked_user_id": "Another contact is already linked to this account."
                    }
                )
        return attrs

    def create(self, validated_data):
        scope = validated_data.pop("scope", None) or {
            "owner": self.context["request"].user
        }
        try:
            with transaction.atomic():
                return create_person(**scope, **validated_data)
        except IntegrityError as exc:
            raise _linked_user_clash() from exc

    def update(self, instance, validated_data):
        scope = validated_data.pop("scope", None)
        # `validate` checked the scope holds no other contact for that account,
        # but a concurrent write can land between the check and here: the
        # partial unique constraints are what actually decides, and their
        # IntegrityError is the same refusal, not a 500.
        try:
            with transaction.atomic():
                if validated_data:
                    update_person(instance, **validated_data)
                if scope is not None:
                    target_owner = scope.get("owner")
                    target_group = scope.get("group")
                    if (target_owner, target_group) != (
                        instance.owner,
                        instance.group,
                    ):
                        move_to_scope(instance, owner=target_owner, group=target_group)
        except IntegrityError as exc:
            raise _linked_user_clash() from exc
        return instance


class PersonListSerializer(serializers.ModelSerializer):
    scope = serializers.CharField(required=False)
    member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = PersonList
        fields = ["uuid", "name", "scope", "member_count", "created_at"]
        read_only_fields = ["uuid", "created_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["scope"] = scope_label(instance)
        return data

    def validate_scope(self, value):
        if self.instance is not None:
            raise serializers.ValidationError(
                "A list cannot change scope; create one in the other address book."
            )
        return parse_scope(self.context["request"].user, value)


class ListMembersSerializer(serializers.Serializer):
    uuids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=200
    )
