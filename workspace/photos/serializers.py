from django.urls import reverse
from rest_framework import serializers

from .models import Album, Face, FaceCluster


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


def _crop_url(face_id):
    return reverse("photos-face-crop", kwargs={"pk": face_id}) if face_id else None


class FaceClusterSerializer(serializers.ModelSerializer):
    photo_count = serializers.IntegerField(
        read_only=True, help_text="Photos in the library showing this person."
    )
    cover = serializers.PrimaryKeyRelatedField(
        queryset=Face.objects.none(),
        allow_null=False,
        help_text="The face shown for the cluster: one of its own.",
    )
    cover_url = serializers.SerializerMethodField(help_text="URL of the cover crop.")
    person = serializers.UUIDField(
        source="person_id",
        allow_null=True,
        required=False,
        help_text=(
            "The contact (People) this cluster is; write one to name the "
            "cluster, null to clear its name."
        ),
    )
    person_name = serializers.SerializerMethodField(
        help_text="The contact's name, or null for an unnamed cluster."
    )
    new_person = serializers.CharField(
        write_only=True,
        required=False,
        max_length=255,
        trim_whitespace=True,
        help_text="Name the cluster after a new personal contact of this name.",
    )

    class Meta:
        model = FaceCluster
        fields = [
            "uuid",
            "hidden",
            "photo_count",
            "face_count",
            "cover",
            "cover_url",
            "person",
            "person_name",
            "new_person",
            "created_at",
        ]
        read_only_fields = ["uuid", "face_count", "created_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cluster = self.instance if isinstance(self.instance, FaceCluster) else None
        if cluster is not None:
            self.fields["cover"].queryset = Face.objects.filter(cluster=cluster)

    def get_cover_url(self, cluster):
        return _crop_url(cluster.cover_id)

    def get_person_name(self, cluster) -> str | None:
        return cluster.person.display_name if cluster.person_id else None


class FaceClusterCreateSerializer(serializers.Serializer):
    face = serializers.UUIDField(
        help_text="The face the new cluster starts from; it leaves its current one."
    )


class FaceClusterMergeSerializer(serializers.Serializer):
    clusters = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        max_length=100,
        help_text="The clusters to fold into this one; they are deleted.",
    )
    person = serializers.UUIDField(
        required=False,
        help_text=(
            "The contact the merged cluster is named after, when the clusters "
            "are named after different people: one of theirs."
        ),
    )


class FaceClusterReviewSerializer(serializers.Serializer):
    confirmed = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        max_length=500,
        help_text="Faces of the cluster that are this person.",
    )
    rejected = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        max_length=500,
        help_text="Faces of the cluster that are not this person.",
    )

    def validate(self, data):
        if set(data["confirmed"]) & set(data["rejected"]):
            raise serializers.ValidationError(
                "A face cannot be both confirmed and rejected."
            )
        return data


class FaceSerializer(serializers.ModelSerializer):
    file = serializers.UUIDField(source="file_id", read_only=True)
    box = serializers.SerializerMethodField(
        help_text="x, y, width, height as fractions of the displayed photo."
    )
    cluster = serializers.UUIDField(
        source="cluster_id",
        allow_null=True,
        required=False,
        help_text=(
            "Write a cluster to say this face is that person (the assignment "
            "becomes 'confirmed'); write null to say it is not the person of "
            "its current cluster (it becomes 'rejected')."
        ),
    )
    crop_url = serializers.SerializerMethodField()
    to_person = serializers.UUIDField(
        write_only=True,
        required=False,
        help_text=(
            "Say this face is that contact: it joins the contact's closest "
            "cluster, or a new one of theirs."
        ),
    )
    new_person = serializers.CharField(
        write_only=True,
        required=False,
        max_length=255,
        trim_whitespace=True,
        help_text="Say this face is someone new: a contact of this name is created.",
    )

    class Meta:
        model = Face
        fields = [
            "uuid",
            "file",
            "box",
            "quality",
            "cluster",
            "assignment",
            "crop_url",
            "to_person",
            "new_person",
        ]
        read_only_fields = ["uuid", "quality"]
        extra_kwargs = {
            "assignment": {
                "help_text": (
                    "Write 'confirmed' to pin the face in its current cluster."
                ),
            },
        }

    def get_box(self, face):
        return {
            "x": face.box_x,
            "y": face.box_y,
            "width": face.box_width,
            "height": face.box_height,
        }

    def get_crop_url(self, face):
        return _crop_url(face.pk)

    def validate_assignment(self, value):
        if value != Face.Assignment.CONFIRMED:
            raise serializers.ValidationError(
                "Only 'confirmed' can be written; write cluster: null to reject."
            )
        return value


class FaceBatchSerializer(serializers.Serializer):
    ACTIONS = ("confirm", "reject", "hide", "unhide", "assign")

    faces = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, max_length=500
    )
    action = serializers.ChoiceField(choices=ACTIONS)
    cluster = serializers.UUIDField(
        required=False, help_text="assign: these faces are this cluster's person."
    )
    person = serializers.UUIDField(
        required=False, help_text="assign: these faces are this contact."
    )
    new_person = serializers.CharField(
        required=False,
        max_length=255,
        trim_whitespace=True,
        help_text="assign: these faces are someone new, a contact of this name.",
    )
    new_cluster = serializers.BooleanField(
        required=False,
        default=False,
        help_text="assign: these faces are someone new, not named yet.",
    )

    def validate(self, data):
        targets = [
            key for key in ("cluster", "person", "new_person") if data.get(key)
        ] + (["new_cluster"] if data["new_cluster"] else [])
        if data["action"] == "assign" and len(targets) != 1:
            raise serializers.ValidationError(
                "assign takes exactly one of cluster, person, new_person, new_cluster."
            )
        if data["action"] != "assign" and targets:
            raise serializers.ValidationError("Only assign takes a target.")
        return data


class FaceUndoSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=64)
