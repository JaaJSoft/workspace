"""Favorites and pinned folders for FileViewSet."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from workspace.files.models import File, PinnedFolder
from workspace.files.serializers import (
    FileMarksResultSerializer,
    FileMarksSerializer,
    PinnedReorderSerializer,
)
from workspace.files.services import FileService
from workspace.files.services.marks import set_favorite, set_pinned
from workspace.files.services.pins import visible_pins


class FavoritesMixin:
    """Adds the favorites and pinned folders endpoints."""

    @extend_schema(
        summary="Add to favorites or remove from them",
        description=(
            "Mark the given files and folders as the caller's favorites, or "
            "take them out. The answer lists the items that are now as asked; "
            "an item the caller may not mark is left out of it, exactly like "
            "an item that does not exist."
        ),
        request=FileMarksSerializer,
        responses={200: FileMarksResultSerializer},
    )
    @action(detail=False, methods=["post"], url_path="favorites")
    def favorites(self, request):
        serializer = FileMarksSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        done = set_favorite(request.user, data["items"], data["value"])
        return Response({"items": done})

    @extend_schema(
        summary="List pinned folders",
        description="Return the current user's pinned folders for the sidebar.",
        responses={
            200: OpenApiResponse(description="List of pinned folders."),
        },
    )
    @action(detail=False, methods=["get"], url_path="pinned")
    def pinned(self, request):
        """List pinned folders."""
        folder_ids = list(
            visible_pins(request.user).values_list("folder_id", flat=True)
        )
        # ``self.get_queryset()`` is owner-scoped (group__isnull=True) and would
        # silently drop any pinned group folder.
        queryset = FileService.annotate_for_serializer(
            File.objects.filter(pk__in=folder_ids), request.user
        )
        # Preserve pin order
        order_map = {fid: i for i, fid in enumerate(folder_ids)}
        folders = sorted(queryset, key=lambda f: order_map.get(f.pk, 0))
        serializer = self.get_serializer(folders, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Pin folders to the sidebar or unpin them",
        description=(
            "Pin the given folders to the caller's sidebar, after the "
            "existing pins and in the order given, or unpin them. The answer "
            "lists the folders that are now as asked; an item the caller may "
            "not pin - a file, a group's root folder, a folder out of reach - "
            "is left out of it, exactly like an item that does not exist."
        ),
        request=FileMarksSerializer,
        responses={200: FileMarksResultSerializer},
    )
    @pinned.mapping.post
    def set_pins(self, request):
        serializer = FileMarksSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        done = set_pinned(request.user, data["items"], data["value"])
        return Response({"items": done})

    @extend_schema(
        summary="Reorder pinned folders",
        description="Update the order of pinned folders. Send an array of folder UUIDs in the desired order.",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "order": {
                        "type": "array",
                        "items": {"type": "string", "format": "uuid"},
                        "description": "Array of folder UUIDs in the desired order",
                    },
                },
                "required": ["order"],
            },
        },
        responses={
            200: OpenApiResponse(description="Order updated successfully."),
            400: OpenApiResponse(description="Invalid request."),
        },
    )
    @action(detail=False, methods=["post"], url_path="pinned/reorder")
    def pinned_reorder(self, request):
        """Reorder pinned folders."""
        serializer = PinnedReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.validated_data["order"]  # list[UUID]

        # Fetch existing pins, preserving previous order for any that the
        # caller did not mention (handles concurrent unpin gracefully -
        # silently skipping unknown UUIDs is fine, but unmentioned pins must
        # land somewhere or we get duplicate positions).
        pins = list(
            PinnedFolder.objects.filter(owner=request.user).order_by(
                "position", "created_at"
            )
        )
        by_uuid = {p.folder_id: p for p in pins}

        new_sequence = []
        seen = set()
        for u in order:
            pin = by_uuid.get(u)
            if pin is not None:
                new_sequence.append(pin)
                seen.add(u)
        for p in pins:
            if p.folder_id not in seen:
                new_sequence.append(p)

        # Renumber positions deterministically 0..N-1 so duplicates cannot
        # appear, regardless of which subset the caller listed.
        to_update = []
        for i, pin in enumerate(new_sequence):
            if pin.position != i:
                pin.position = i
                to_update.append(pin)

        if to_update:
            PinnedFolder.objects.bulk_update(to_update, ["position"])

        return Response({"success": True}, status=status.HTTP_200_OK)
