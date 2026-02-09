from django.contrib.auth import password_validation, update_session_auth_hash
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.http import FileResponse, HttpResponse
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models import Q

from workspace.users import avatar_service
from workspace.users.models import UserSetting


@extend_schema(tags=['Users'])
class UserSearchView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Search users",
        description="Search for users by username, first name, or last name. Excludes the current user and inactive users.",
        parameters=[
            OpenApiParameter(name='q', type=str, required=True, description='Search query (min 2 chars)'),
            OpenApiParameter(name='limit', type=int, required=False, description='Max results (default 10)'),
        ],
        responses={
            200: inline_serializer(
                name='UserSearchResponse',
                fields={
                    'results': serializers.ListField(
                        child=inline_serializer(
                            name='UserSearchItem',
                            fields={
                                'id': serializers.IntegerField(),
                                'username': serializers.CharField(),
                                'first_name': serializers.CharField(),
                                'last_name': serializers.CharField(),
                            },
                        ),
                    ),
                },
            ),
        },
    )
    def get(self, request):
        query = request.query_params.get('q', '').strip()
        if len(query) < 2:
            return Response({'results': []})

        try:
            limit = int(request.query_params.get('limit', 10))
        except (TypeError, ValueError):
            limit = 10
        limit = min(max(limit, 1), 50)

        users = User.objects.filter(
            Q(username__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query),
            is_active=True,
        ).exclude(pk=request.user.pk)[:limit]

        results = []
        for u in users:
            entry = {
                'id': u.id,
                'username': u.username,
                'first_name': u.first_name,
                'last_name': u.last_name,
                'avatar_url': f'/api/v1/users/{u.id}/avatar' if avatar_service.has_avatar(u) else None,
            }
            results.append(entry)

        return Response({'results': results})


@extend_schema(tags=['Users'])
class UserMeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get current user",
        description="Return profile information for the authenticated user.",
        responses={
            200: inline_serializer(
                name='UserMe',
                fields={
                    'username': serializers.CharField(),
                    'email': serializers.EmailField(),
                    'first_name': serializers.CharField(),
                    'last_name': serializers.CharField(),
                    'date_joined': serializers.DateTimeField(),
                    'last_login': serializers.DateTimeField(allow_null=True),
                },
            ),
        },
    )
    def get(self, request):
        user = request.user
        return Response({
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'date_joined': user.date_joined,
            'last_login': user.last_login,
        })


@extend_schema(tags=['Users'])
class PasswordRulesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get password rules",
        description="Return the list of password validation rules configured on the server.",
        responses={
            200: inline_serializer(
                name='PasswordRules',
                fields={
                    'rules': serializers.ListField(child=serializers.CharField()),
                },
            ),
        },
    )
    def get(self, request):
        rules = password_validation.password_validators_help_texts()
        return Response({'rules': rules})


@extend_schema(tags=['Users'])
class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Change password",
        description=(
            "Change the authenticated user's password. "
            "Validates the current password, then applies Django's password validators to the new one. "
            "The session is preserved after a successful change."
        ),
        request=inline_serializer(
            name='ChangePasswordRequest',
            fields={
                'current_password': serializers.CharField(),
                'new_password': serializers.CharField(),
            },
        ),
        responses={
            200: inline_serializer(
                name='ChangePasswordSuccess',
                fields={
                    'message': serializers.CharField(),
                },
            ),
            400: OpenApiResponse(
                description="Validation error.",
                response=inline_serializer(
                    name='ChangePasswordError',
                    fields={
                        'errors': serializers.ListField(child=serializers.CharField()),
                    },
                ),
            ),
        },
    )
    def post(self, request):
        current_password = request.data.get('current_password', '')
        new_password = request.data.get('new_password', '')

        if not current_password or not new_password:
            return Response(
                {'errors': ['Current password and new password are required.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not request.user.check_password(current_password):
            return Response(
                {'errors': ['Current password is incorrect.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            password_validation.validate_password(new_password, request.user)
        except Exception as e:
            return Response(
                {'errors': list(e.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request.user.set_password(new_password)
        request.user.save()
        update_session_auth_hash(request, request.user)

        return Response({'message': 'Password updated successfully.'})


AVATAR_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
AVATAR_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@extend_schema(tags=['Users'])
class UserAvatarRetrieveView(APIView):
    """Serve a user's avatar image (public)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        summary="Get user avatar",
        description="Serve the avatar image for a given user. Returns 404 if no avatar exists.",
        responses={
            200: OpenApiResponse(description="Avatar image (WebP)"),
            304: OpenApiResponse(description="Not modified"),
            404: OpenApiResponse(description="No avatar found"),
        },
    )
    def get(self, request, user_id):
        path = avatar_service.get_avatar_path(user_id)
        if not default_storage.exists(path):
            return HttpResponse(status=404)

        etag = avatar_service.get_avatar_etag(user_id)
        if etag:
            if_none_match = request.META.get("HTTP_IF_NONE_MATCH")
            if if_none_match and if_none_match.strip('"') == etag:
                response = HttpResponse(status=304)
                response["ETag"] = f'"{etag}"'
                return response

        avatar_file = default_storage.open(path, "rb")
        response = FileResponse(avatar_file, content_type="image/webp")
        response["Cache-Control"] = "no-cache"
        if etag:
            response["ETag"] = f'"{etag}"'
        return response


@extend_schema(tags=['Users'])
class UserAvatarUploadView(APIView):
    """Upload or delete the authenticated user's avatar."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    @extend_schema(
        summary="Upload avatar",
        description="Upload a profile picture. Crop coordinates are applied server-side.",
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "image": {"type": "string", "format": "binary"},
                    "crop_x": {"type": "number"},
                    "crop_y": {"type": "number"},
                    "crop_w": {"type": "number"},
                    "crop_h": {"type": "number"},
                },
                "required": ["image", "crop_x", "crop_y", "crop_w", "crop_h"],
            }
        },
        responses={
            200: inline_serializer(
                name="AvatarUploadResponse",
                fields={"message": serializers.CharField()},
            ),
            400: OpenApiResponse(description="Validation error"),
        },
    )
    def post(self, request):
        image = request.FILES.get("image")
        if not image:
            return Response(
                {"errors": ["No image provided."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if image.content_type not in AVATAR_ALLOWED_TYPES:
            return Response(
                {"errors": ["Unsupported image type. Use JPEG, PNG, WebP, or GIF."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if image.size > AVATAR_MAX_SIZE:
            return Response(
                {"errors": ["Image too large. Maximum size is 10 MB."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            crop_x = float(request.data.get("crop_x", 0))
            crop_y = float(request.data.get("crop_y", 0))
            crop_w = float(request.data.get("crop_w", 0))
            crop_h = float(request.data.get("crop_h", 0))
        except (TypeError, ValueError):
            return Response(
                {"errors": ["Invalid crop coordinates."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if crop_w <= 0 or crop_h <= 0:
            return Response(
                {"errors": ["Crop width and height must be positive."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        avatar_service.process_and_save_avatar(
            request.user, image, crop_x, crop_y, crop_w, crop_h,
        )
        return Response({"message": "Avatar updated successfully."})

    @extend_schema(
        summary="Delete avatar",
        description="Remove the authenticated user's profile picture.",
        responses={200: inline_serializer(
            name="AvatarDeleteResponse",
            fields={"message": serializers.CharField()},
        )},
    )
    def delete(self, request):
        avatar_service.delete_avatar(request.user)
        return Response({"message": "Avatar removed."})




# ── Settings API ──────────────────────────────────────────────

_setting_fields = {
    'module': serializers.CharField(),
    'key': serializers.CharField(),
    'value': serializers.JSONField(allow_null=True),
}


@extend_schema(tags=['Settings'])
class SettingsListView(APIView):
    """List all settings for the authenticated user, optionally filtered by module."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List user settings",
        parameters=[
            OpenApiParameter(name='module', type=str, required=False),
        ],
        responses={
            200: inline_serializer(
                name='SettingsListResponse',
                fields={
                    'results': serializers.ListField(
                        child=inline_serializer(
                            name='SettingItem',
                            fields=_setting_fields,
                        ),
                    ),
                },
            ),
        },
    )
    def get(self, request):
        module = request.query_params.get('module')
        qs = UserSetting.objects.filter(user=request.user)
        if module:
            qs = qs.filter(module=module)
        results = list(qs.values('module', 'key', 'value'))
        return Response({'results': results})


@extend_schema(tags=['Settings'])
class SettingDetailView(APIView):
    """Read, write or delete a single setting identified by module + key."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get a setting",
        responses={
            200: inline_serializer(name='SettingDetail', fields=_setting_fields),
            404: OpenApiResponse(description="Setting not found."),
        },
    )
    def get(self, request, module, key):
        row = UserSetting.objects.filter(
            user=request.user, module=module, key=key,
        ).values('module', 'key', 'value').first()
        if not row:
            return Response(
                {'detail': 'Setting not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(row)

    @extend_schema(
        summary="Create or update a setting",
        request=inline_serializer(
            name='SettingWriteRequest',
            fields={'value': serializers.JSONField(allow_null=True)},
        ),
        responses={
            200: inline_serializer(name='SettingWriteResponse', fields=_setting_fields),
        },
    )
    def put(self, request, module, key):
        value = request.data.get('value')
        obj, _ = UserSetting.objects.update_or_create(
            user=request.user, module=module, key=key,
            defaults={'value': value},
        )
        return Response({'module': obj.module, 'key': obj.key, 'value': obj.value})

    @extend_schema(
        summary="Delete a setting",
        responses={204: None},
    )
    def delete(self, request, module, key):
        deleted, _ = UserSetting.objects.filter(
            user=request.user, module=module, key=key,
        ).delete()
        if not deleted:
            return Response(
                {'detail': 'Setting not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
