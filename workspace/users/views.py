from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib.auth import password_validation, update_session_auth_hash
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.http import FileResponse, HttpResponse
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from knox.models import AuthToken
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.files.models import File
from workspace.users.models import APITokenLabel, UserSetting
from workspace.users.services import avatar as avatar_service, presence as presence_service
from workspace.users.services.settings import delete_setting, set_setting


@extend_schema(tags=['Users'])
class UserSearchView(CacheControlMixin, APIView):
    permission_classes = [IsAuthenticated]
    cache_max_age = 60

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
            bot_profile__isnull=True,
        ).exclude(pk=request.user.pk)[:limit]

        results = [
            {
                'id': u.id,
                'username': u.username,
                'first_name': u.first_name,
                'last_name': u.last_name,
            }
            for u in users
        ]

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
                    'rules': serializers.ListField(
                        child=inline_serializer(
                            name='PasswordRule',
                            fields={
                                'text': serializers.CharField(),
                                'code': serializers.CharField(),
                                'value': serializers.IntegerField(required=False),
                            },
                        )
                    ),
                },
            ),
        },
    )
    def get(self, request):
        validators = password_validation.get_password_validators(
            django_settings.AUTH_PASSWORD_VALIDATORS
        )
        code_map = {
            'MinimumLengthValidator': 'min_length',
            'NumericPasswordValidator': 'numeric',
            'CommonPasswordValidator': 'common',
            'UserAttributeSimilarityValidator': 'similarity',
        }
        rules = []
        for v in validators:
            class_name = v.__class__.__name__
            rule = {
                'text': v.get_help_text(),
                'code': code_map.get(class_name, 'custom'),
            }
            if class_name == 'MinimumLengthValidator':
                rule['value'] = v.min_length
            rules.append(rule)
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
        if not User.objects.filter(pk=user_id, is_active=True).exists():
            return HttpResponse(status=404)
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



@extend_schema(tags=['Users'])
class UserStatusView(APIView):
    """Get or set the authenticated user's manual presence status."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get manual status",
        description="Return the user's current manual presence status.",
        responses={
            200: inline_serializer(
                name='UserStatusGetResponse',
                fields={'status': serializers.CharField()},
            ),
        },
    )
    def get(self, request):
        current = presence_service.get_manual_status(request.user.pk)
        return Response({'status': current})

    @extend_schema(
        summary="Set manual status",
        description="Set the user's manual presence status (auto, online, away, busy, invisible).",
        request=inline_serializer(
            name='UserStatusRequest',
            fields={'status': serializers.ChoiceField(choices=['auto', 'online', 'away', 'busy', 'invisible'])},
        ),
        responses={
            200: inline_serializer(
                name='UserStatusResponse',
                fields={'status': serializers.CharField()},
            ),
        },
    )
    def put(self, request):
        new_status = request.data.get('status', '')
        if new_status not in presence_service.VALID_MANUAL_STATUSES:
            return Response(
                {'errors': [f'Invalid status. Choose from: {", ".join(sorted(presence_service.VALID_MANUAL_STATUSES))}']},
                status=status.HTTP_400_BAD_REQUEST,
            )
        presence_service.set_manual_status(request.user.pk, new_status)
        return Response({'status': new_status})


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
        obj = set_setting(request.user, module, key, value)
        return Response({'module': obj.module, 'key': obj.key, 'value': obj.value})

    @extend_schema(
        summary="Delete a setting",
        responses={204: None},
    )
    def delete(self, request, module, key):
        if not delete_setting(request.user, module, key):
            return Response(
                {'detail': 'Setting not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── API Tokens ───────────────────────────────────────────────

@extend_schema(tags=['Auth'])
class APITokenListCreateView(APIView):
    """List and create API tokens for the authenticated user."""

    permission_classes = [IsAuthenticated]

    pagination_class = None

    @extend_schema(
        summary="List API tokens",
        description="Return all active (non-expired) API tokens for the current user.",
        responses={
            200: inline_serializer(
                name='APITokenItem',
                many=True,
                fields={
                    'id': serializers.CharField(),
                    'name': serializers.CharField(),
                    'token_key': serializers.CharField(),
                    'created': serializers.DateTimeField(),
                    'expiry': serializers.DateTimeField(allow_null=True),
                },
            ),
        },
    )
    def get(self, request):
        from django.utils.timezone import now

        tokens = AuthToken.objects.filter(user=request.user).select_related('label')
        # Exclude expired tokens
        tokens = tokens.filter(
            Q(expiry__isnull=True) | Q(expiry__gt=now()),
        )
        results = []
        for t in tokens:
            label = getattr(t, 'label', None)
            results.append({
                'id': t.pk,
                'name': label.name if label else '',
                'token_key': t.token_key,
                'created': t.created,
                'expiry': t.expiry,
            })
        return Response(results)

    @extend_schema(
        summary="Create an API token",
        description=(
            "Create a new API token. The full token value is returned **only once** in the response. "
            "Use `Authorization: Token <value>` to authenticate."
        ),
        request=inline_serializer(
            name='APITokenCreateRequest',
            fields={
                'name': serializers.CharField(required=False, help_text="Label for the token"),
                'expiry_days': serializers.IntegerField(
                    required=False,
                    help_text="Token lifetime in days. Omit for no expiration.",
                ),
            },
        ),
        responses={
            201: inline_serializer(
                name='APITokenCreateResponse',
                fields={
                    'id': serializers.CharField(),
                    'name': serializers.CharField(),
                    'token': serializers.CharField(help_text="Full token (shown once)"),
                    'token_key': serializers.CharField(),
                    'expiry': serializers.DateTimeField(allow_null=True),
                },
            ),
        },
    )
    def post(self, request):
        name = request.data.get('name', '')
        expiry_days = request.data.get('expiry_days')

        expiry = None
        if expiry_days is not None:
            try:
                expiry_days = int(expiry_days)
                if expiry_days < 1:
                    raise ValueError
            except (TypeError, ValueError):
                return Response(
                    {'errors': ['expiry_days must be a positive integer.']},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            expiry = timedelta(days=expiry_days)

        with transaction.atomic():
            instance, token = AuthToken.objects.create(user=request.user, expiry=expiry)
            APITokenLabel.objects.create(auth_token=instance, name=name)

        return Response(
            {
                'id': instance.pk,
                'name': name,
                'token': token,
                'token_key': instance.token_key,
                'expiry': instance.expiry,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=['Auth'])
class APITokenDetailView(APIView):
    """Revoke (delete) a single API token."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Revoke an API token",
        responses={204: None, 404: OpenApiResponse(description="Token not found.")},
    )
    def delete(self, request, pk):
        deleted, _ = AuthToken.objects.filter(user=request.user, pk=pk).delete()
        if not deleted:
            return Response(
                {'detail': 'Token not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserGroupsView(APIView):
    """List the current user's Django groups with folder status."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List current user's groups",
        tags=['Users'],
    )
    def get(self, request):
        groups = request.user.groups.all()
        folder_subquery = File.objects.filter(
            group_id=OuterRef('pk'),
            parent__isnull=True,
            deleted_at__isnull=True,
        )
        groups = groups.annotate(has_folder=Exists(folder_subquery))
        data = [
            {
                'id': g.id,
                'name': g.name,
                'has_folder': g.has_folder,
            }
            for g in groups
        ]
        return Response(data)
