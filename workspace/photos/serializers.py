from django.urls import reverse
from rest_framework import serializers

from .models import Album


class AlbumSerializer(serializers.Serializer):
    """An album as the viewer sees it, from a ``services.album_cards.AlbumCard``."""

    uuid = serializers.UUIDField(source="album.uuid")
    title = serializers.CharField(source="album.title")
    description = serializers.CharField(source="album.description")
    sort_mode = serializers.CharField(source="album.sort_mode")
    group = serializers.IntegerField(source="album.group_id", allow_null=True)
    count = serializers.IntegerField()
    cover = serializers.SerializerMethodField()
    cover_url = serializers.CharField()
    url = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(source="album.created_at")
    updated_at = serializers.DateTimeField(source="album.updated_at")

    def get_cover(self, card) -> str | None:
        return str(card.cover.uuid) if card.cover is not None else None

    def get_url(self, card) -> str:
        return reverse("photos_ui:album", args=[card.album.uuid])


class AlbumWriteSerializer(serializers.ModelSerializer):
    """The fields a client writes directly. The cover and the items have
    their own checks, in the views."""

    title = serializers.CharField(max_length=255, trim_whitespace=True)
    description = serializers.CharField(
        required=False, allow_blank=True, max_length=2000, trim_whitespace=True
    )

    class Meta:
        model = Album
        fields = ["title", "description", "sort_mode"]
