"""REST endpoints for sharing albums: members, leaving, public links.

Every member may read who else is in the album. Changing the members or the
links answers to the album's ``share`` action, the same gate its menu reads
(``POST /api/v1/photos/albums/actions``). An album the caller cannot open is
a 404, never a 403.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.projects.models import Project
from workspace.projects.queries import user_project_ids

from ..actions import AlbumActionRegistry
from ..models import AlbumLink, AlbumShare
from ..queries import direct_share_album_ids, get_album_role, reachable_album
from ..services.album_sharing import (
    create_link,
    leave_album,
    revoke_link,
    share_album,
    unshare_album,
)

User = get_user_model()


class ShareTargetSerializer(serializers.Serializer):
    shared_with = serializers.IntegerField(required=False, help_text="A user id.")
    group = serializers.IntegerField(required=False, help_text="A group id.")
    project = serializers.UUIDField(required=False, help_text="A project uuid.")

    def validate(self, attrs):
        if len(attrs) != 1:
            raise serializers.ValidationError(
                "Give exactly one of shared_with, group and project."
            )
        return attrs


class ShareWriteSerializer(ShareTargetSerializer):
    role = serializers.ChoiceField(choices=AlbumShare.Role.choices)

    def validate(self, attrs):
        targets = {k: v for k, v in attrs.items() if k != "role"}
        super().validate(targets)
        return attrs


class LinkWriteSerializer(serializers.Serializer):
    password = serializers.CharField(
        required=False, allow_blank=True, max_length=128, trim_whitespace=False
    )
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    allow_download = serializers.BooleanField(required=False, default=False)

    def validate_expires_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("The expiry must be in the future.")
        return value


def _not_found():
    return Response(status=status.HTTP_404_NOT_FOUND)


def _album_for(request, uuid, *, action=None):
    """``(album, None)`` when the caller may open the album (and run
    *action* on it), else ``(None, error response)``."""
    album = reachable_album(request.user, uuid)
    if album is None:
        return None, _not_found()
    if action is not None and not AlbumActionRegistry.is_action_available(
        action,
        request.user,
        album,
        role=get_album_role(request.user, album),
        direct=bool(direct_share_album_ids(request.user, [album])),
    ):
        return None, Response(status=status.HTTP_403_FORBIDDEN)
    return album, None


def _user_entry(user):
    return {
        "type": "user",
        "id": user.pk,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


def _share_entry(share):
    if share.shared_with_id is not None:
        entry = _user_entry(share.shared_with)
    elif share.shared_with_group_id is not None:
        group = share.shared_with_group
        entry = {"type": "group", "id": group.pk, "name": group.name}
    else:
        project = share.shared_with_project
        entry = {"type": "project", "id": str(project.pk), "name": project.name}
    return entry | {"role": share.role, "shared_at": share.created_at}


def _resolve_target(request, data):
    """The share target *data* names, as keyword arguments for the sharing
    service, or a Response refusing it."""
    if "shared_with" in data:
        if data["shared_with"] == request.user.pk:
            return Response(
                {"detail": "Cannot share with yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = User.objects.filter(pk=data["shared_with"], is_active=True).first()
        return {"user": user} if user is not None else _not_found()
    if "group" in data:
        group = Group.objects.filter(pk=data["group"]).first()
        return {"group": group} if group is not None else _not_found()
    project = Project.objects.filter(
        uuid=data["project"], uuid__in=user_project_ids(request.user)
    ).first()
    return {"project": project} if project is not None else _not_found()


@extend_schema(tags=["Photos - Album sharing"])
class AlbumSharesView(CacheControlMixin, APIView):
    cache_no_store = True

    @extend_schema(
        summary="List an album's members",
        description=(
            "Who holds the album (`owner`: a user, or the group of a group "
            "album) and who it is shared with, each with their role. "
            "`allow_download` says whether viewers may download."
        ),
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)},
    )
    def get(self, request, uuid):
        album, error = _album_for(request, uuid)
        if error:
            return error
        if album.group_id is not None:
            owner = {"type": "group", "id": album.group_id, "name": album.group.name}
        else:
            owner = _user_entry(album.owner)
        shares = AlbumShare.objects.filter(album=album).select_related(
            "shared_with", "shared_with_group", "shared_with_project"
        )
        return Response(
            {
                "owner": owner,
                "shares": [_share_entry(s) for s in shares.order_by("created_at")],
                "allow_download": album.allow_download,
            }
        )

    @extend_schema(
        summary="Share an album or change a member's role",
        description=(
            "Add a user (`shared_with`), a group (`group`) or a project "
            "(`project`) with a `role` (viewer, contributor or manager), or "
            "change the role of one already there. The people it reaches are "
            "notified."
        ),
        request=ShareWriteSerializer,
        responses={
            201: OpenApiResponse(response=OpenApiTypes.OBJECT),
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
        },
    )
    def post(self, request, uuid):
        album, error = _album_for(request, uuid, action="share")
        if error:
            return error
        serializer = ShareWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = _resolve_target(request, serializer.validated_data)
        if isinstance(target, Response):
            return target
        if target.get("user") is not None and (
            album.group_id is None and target["user"].pk == album.owner_id
        ):
            return Response(
                {"detail": "The owner already holds the album."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        share, created = share_album(
            album,
            role=serializer.validated_data["role"],
            acting_user=request.user,
            **target,
        )
        return Response(
            _share_entry(share),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        summary="Remove a member from an album",
        description="The photos they added stay in the album.",
        request=ShareTargetSerializer,
        responses={204: None},
    )
    def delete(self, request, uuid):
        album, error = _album_for(request, uuid, action="share")
        if error:
            return error
        serializer = ShareTargetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = _resolve_target(request, serializer.validated_data)
        if isinstance(target, Response):
            return target
        if not unshare_album(album, acting_user=request.user, **target):
            return _not_found()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Photos - Album sharing"])
class AlbumLeaveView(APIView):
    @extend_schema(
        summary="Leave an album",
        description=(
            "Give up the caller's own share of the album. With "
            "`remove_items`, the photos they added leave with them; "
            "otherwise they stay in it."
        ),
        request={
            "application/json": {
                "type": "object",
                "properties": {"remove_items": {"type": "boolean"}},
            }
        },
        responses={204: None},
    )
    def post(self, request, uuid):
        album, error = _album_for(request, uuid, action="leave")
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        leave_album(album, request.user, remove_items=data.get("remove_items") is True)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _link_entry(request, link):
    return {
        "uuid": str(link.uuid),
        "url": request.build_absolute_uri(
            reverse("photos_ui:shared_album", args=[link.token])
        ),
        "has_password": link.has_password,
        "expires_at": link.expires_at,
        "allow_download": link.allow_download,
        "view_count": link.view_count,
        "last_accessed_at": link.last_accessed_at,
        "created_at": link.created_at,
    }


@extend_schema(tags=["Photos - Album sharing"])
class AlbumLinksView(CacheControlMixin, APIView):
    cache_no_store = True

    @extend_schema(
        summary="List an album's public links",
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)},
    )
    def get(self, request, uuid):
        album, error = _album_for(request, uuid, action="share")
        if error:
            return error
        links = AlbumLink.objects.filter(album=album).order_by("-created_at")
        return Response([_link_entry(request, link) for link in links])

    @extend_schema(
        summary="Create a public link to an album",
        description=(
            "Anyone with the link sees the album without an account. "
            "`password` and `expires_at` are optional; `allow_download` "
            "lets visitors download the originals, EXIF included."
        ),
        request=LinkWriteSerializer,
        responses={201: OpenApiResponse(response=OpenApiTypes.OBJECT)},
    )
    def post(self, request, uuid):
        album, error = _album_for(request, uuid, action="share")
        if error:
            return error
        serializer = LinkWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = create_link(album, acting_user=request.user, **serializer.validated_data)
        return Response(_link_entry(request, link), status=status.HTTP_201_CREATED)


@extend_schema(tags=["Photos - Album sharing"])
class AlbumLinkDetailView(APIView):
    @extend_schema(summary="Revoke a public link", responses={204: None})
    def delete(self, request, uuid, link_uuid):
        album, error = _album_for(request, uuid, action="share")
        if error:
            return error
        if not revoke_link(album, link_uuid):
            return _not_found()
        return Response(status=status.HTTP_204_NO_CONTENT)
