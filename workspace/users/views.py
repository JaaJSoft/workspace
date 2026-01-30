from django.contrib.auth import password_validation, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


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


@login_required
def profile_view(request):
    return render(request, 'users/profile/index.html')
