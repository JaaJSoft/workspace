from django.db import IntegrityError, transaction
from django.db.models import Count
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..queries import reachable_list, user_person_lists, user_persons
from ..serializers import ListMembersSerializer, PersonListSerializer
from ..services.lists import (
    ScopeMismatch,
    add_members,
    create_list,
    remove_members,
    rename_list,
)


def _with_counts(qs):
    return qs.annotate(member_count=Count("members"))


def _get_list_or_404(user, uuid):
    person_list = reachable_list(user, uuid)
    if person_list is None:
        return None
    return _with_counts(user_person_lists(user)).get(uuid=uuid)


@extend_schema(tags=["People"])
class PersonListView(APIView):
    @extend_schema(
        summary="List contact lists", responses=PersonListSerializer(many=True)
    )
    def get(self, request):
        qs = _with_counts(user_person_lists(request.user))
        return Response(PersonListSerializer(qs, many=True).data)

    @extend_schema(summary="Create a contact list", request=PersonListSerializer)
    def post(self, request):
        serializer = PersonListSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        scope = serializer.validated_data.get("scope") or {"owner": request.user}
        try:
            with transaction.atomic():
                person_list = create_list(
                    **scope, name=serializer.validated_data["name"]
                )
        except IntegrityError:
            return Response(
                {"name": ["A list with this name already exists."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        person_list.member_count = 0
        return Response(
            PersonListSerializer(person_list).data, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["People"])
class PersonListDetailView(APIView):
    @extend_schema(summary="Get a contact list", responses=PersonListSerializer)
    def get(self, request, uuid):
        person_list = _get_list_or_404(request.user, uuid)
        if person_list is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(PersonListSerializer(person_list).data)

    @extend_schema(summary="Rename a contact list", request=PersonListSerializer)
    def patch(self, request, uuid):
        person_list = _get_list_or_404(request.user, uuid)
        if person_list is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = PersonListSerializer(
            person_list, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data.get("name")
        if name is not None and name != person_list.name:
            try:
                with transaction.atomic():
                    rename_list(person_list, name)
            except IntegrityError:
                return Response(
                    {"name": ["A list with this name already exists."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return Response(PersonListSerializer(person_list).data)

    @extend_schema(summary="Delete a contact list")
    def delete(self, request, uuid):
        person_list = reachable_list(request.user, uuid)
        if person_list is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        person_list.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["People"])
class PersonListMembersView(APIView):
    def _resolve(self, request, uuid):
        person_list = reachable_list(request.user, uuid)
        if person_list is None:
            return None, None, Response(status=status.HTTP_404_NOT_FOUND)
        serializer = ListMembersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wanted = set(serializer.validated_data["uuids"])
        persons = list(user_persons(request.user).filter(uuid__in=wanted))
        if len(persons) != len(wanted):
            return (
                None,
                None,
                Response(
                    {"detail": "One or more persons not found."},
                    status=status.HTTP_404_NOT_FOUND,
                ),
            )
        return person_list, persons, None

    def _respond(self, request, uuid):
        return Response(PersonListSerializer(_get_list_or_404(request.user, uuid)).data)

    @extend_schema(summary="Add persons to a list", request=ListMembersSerializer)
    def post(self, request, uuid):
        person_list, persons, error = self._resolve(request, uuid)
        if error is not None:
            return error
        try:
            add_members(person_list, persons)
        except ScopeMismatch:
            return Response(
                {
                    "detail": "Every person must be in the same address book as the list."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return self._respond(request, uuid)

    @extend_schema(summary="Remove persons from a list", request=ListMembersSerializer)
    def delete(self, request, uuid):
        person_list, persons, error = self._resolve(request, uuid)
        if error is not None:
            return error
        remove_members(person_list, persons)
        return self._respond(request, uuid)
