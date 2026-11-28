import uuid

from django.conf import settings
from django.core.cache import cache
from django.http import Http404
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.generics import (
    GenericAPIView,
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from workspace.common.client_address import client_address
from workspace.common.exceptions import Conflict
from workspace.common.uuids import parse_uuid_or_none
from workspace.notifications.services.notifications import mark_source_read

from ..filters import PollFilter
from ..models import Poll
from ..serializers_polls import (
    GuestVoteSubmitSerializer,
    PollFinalizeSerializer,
    PollSerializer,
    PollWriteSerializer,
    PublicPollSerializer,
    VoteSubmitSerializer,
)
from ..services.polls import (
    PollClosed,
    attach_votes,
    cast_votes,
    create_poll,
    detailed,
    finalize_poll,
    involving,
    update_poll,
    with_details,
)


def _closed():
    return Conflict(_("This poll is closed."))


class PollRowsMixin:
    """A poll, read by whoever holds its uuid - public to the instance by
    design - and changed by its creator alone."""

    queryset = Poll.objects.none()
    serializer_class = PollSerializer
    lookup_field = "uuid"

    def get_queryset(self):
        return with_details(Poll.objects.all())

    def get_object(self):
        try:
            poll = super().get_object()
        except Http404:
            raise NotFound(_("Poll not found.")) from None
        return attach_votes([poll])[0]

    def own_poll(self):
        poll = self.get_object()
        if poll.created_by_id != self.request.user.id:
            raise PermissionDenied(_("Only its creator can change a poll."))
        return poll

    def answer(self, poll, status_code=status.HTTP_200_OK):
        return Response(self.get_serializer(detailed(poll)).data, status=status_code)


@extend_schema(tags=["Calendar - Polls"])
@extend_schema_view(
    get=extend_schema(
        summary="List the polls the caller created, was invited to or voted in",
        description="Newest first.",
    ),
    post=extend_schema(
        summary="Create a poll",
        request=PollWriteSerializer,
        responses={201: PollSerializer},
    ),
)
class PollListView(PollRowsMixin, ListCreateAPIView):
    filterset_class = PollFilter

    def get_queryset(self):
        return with_details(involving(self.request.user)).order_by(
            "-created_at", "uuid"
        )

    def paginate_queryset(self, queryset):
        return attach_votes(super().paginate_queryset(queryset))

    def create(self, request, *args, **kwargs):
        body = PollWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        poll = create_poll(request.user, **body.validated_data)
        return self.answer(poll, status.HTTP_201_CREATED)


@extend_schema(tags=["Calendar - Polls"])
@extend_schema_view(
    get=extend_schema(summary="Get a poll"),
    patch=extend_schema(
        summary="Update a poll",
        description=(
            "Its creator only. The invitees of a poll that chose its time "
            "cannot change anymore."
        ),
        request=PollWriteSerializer,
    ),
    delete=extend_schema(summary="Delete a poll"),
)
class PollDetailView(PollRowsMixin, RetrieveUpdateDestroyAPIView):
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def retrieve(self, request, *args, **kwargs):
        poll = self.get_object()
        mark_source_read(request.user, poll)
        return Response(self.get_serializer(poll).data)

    def update(self, request, *args, **kwargs):
        poll = self.own_poll()
        body = PollWriteSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        try:
            update_poll(poll, request.user, body.validated_data)
        except PollClosed:
            raise _closed() from None
        return self.answer(poll)

    def destroy(self, request, *args, **kwargs):
        self.own_poll().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Calendar - Polls"])
class PollVoteView(PollRowsMixin, GenericAPIView):
    @extend_schema(
        summary="Vote on the times of a poll",
        description=(
            "Each vote replaces the one the caller gave that slot before. "
            "Answers the poll."
        ),
        request=VoteSubmitSerializer,
        responses=PollSerializer,
    )
    def post(self, request, *args, **kwargs):
        poll = self.get_object()
        body = VoteSubmitSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            cast_votes(poll, body.validated_data["votes"], user=request.user)
        except PollClosed:
            raise _closed() from None
        mark_source_read(request.user, poll)
        return self.answer(poll)


@extend_schema(tags=["Calendar - Polls"])
class PollFinalizeView(PollRowsMixin, GenericAPIView):
    @extend_schema(
        summary="Choose a time: close the poll and create its event",
        description="Answers the poll, which now names its `event`.",
        request=PollFinalizeSerializer,
        responses=PollSerializer,
    )
    def post(self, request, *args, **kwargs):
        poll = self.own_poll()
        body = PollFinalizeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        chosen = body.validated_data["slot"]
        slot = next((slot for slot in poll.slots.all() if slot.uuid == chosen), None)
        if slot is None:
            unknown = serializers.PrimaryKeyRelatedField.default_error_messages[
                "does_not_exist"
            ]
            raise ValidationError({"slot": [unknown.format(pk_value=chosen)]})
        try:
            finalize_poll(poll, request.user, slot)
        except PollClosed:
            raise _closed() from None
        return self.answer(poll)


# -- The share link: people without an account --


def _voter_cookie_name(poll):
    return f"poll_voter_{poll.share_token}"


def _guest_voter_token(request, poll):
    """The voter token this browser was issued for *poll*, or ''.

    It only ever travels in the HttpOnly cookie set by the vote endpoint:
    a token taken from the query string or the body would let anyone who
    learned it read and rewrite that guest's votes.
    """
    voter_token = parse_uuid_or_none(request.COOKIES.get(_voter_cookie_name(poll)))
    return str(voter_token) if voter_token else ""


class SharedPollMixin:
    permission_classes = [AllowAny]
    authentication_classes = []
    queryset = Poll.objects.none()
    serializer_class = PublicPollSerializer

    def shared_poll(self):
        poll = (
            with_details(Poll.objects.all())
            .filter(share_token=self.kwargs["token"])
            .first()
        )
        if poll is None:
            raise NotFound(_("Poll not found."))
        return poll

    def answer(self, poll, voter_token):
        context = {**self.get_serializer_context(), "voter_token": voter_token}
        return Response(PublicPollSerializer(detailed(poll), context=context).data)


@extend_schema(tags=["Calendar - Polls (Public)"])
class SharedPollView(SharedPollMixin, GenericAPIView):
    @extend_schema(summary="Get a poll through its share link")
    def get(self, request, *args, **kwargs):
        poll = self.shared_poll()
        return self.answer(poll, _guest_voter_token(request, poll))


@extend_schema(tags=["Calendar - Polls (Public)"])
class SharedPollVoteView(SharedPollMixin, GenericAPIView):
    @extend_schema(
        summary="Vote as a guest of the share link",
        description=(
            "The browser is handed a cookie that stands for the guest: "
            "voting again from it replaces their votes."
        ),
        request=GuestVoteSubmitSerializer,
        responses=PublicPollSerializer,
    )
    def post(self, request, *args, **kwargs):
        poll = self.shared_poll()
        if poll.status != Poll.Status.OPEN:
            raise _closed()

        # Rate limit: max 10 vote submissions per IP per hour
        rate_key = f"poll_vote_rate:{poll.share_token}:{client_address(request)}"
        attempts = cache.get(rate_key, 0)
        if attempts >= 10:
            return Response(
                {"detail": _("Too many vote submissions. Please try again later.")},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        cache.set(rate_key, attempts + 1, 3600)

        body = GuestVoteSubmitSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        voter_token = _guest_voter_token(request, poll) or str(uuid.uuid4())
        try:
            cast_votes(
                poll,
                body.validated_data["votes"],
                voter_token=voter_token,
                guest_name=body.validated_data["guest_name"],
                guest_email=body.validated_data["guest_email"],
            )
        except PollClosed:
            raise _closed() from None

        response = self.answer(poll, voter_token)
        response.set_cookie(
            _voter_cookie_name(poll),
            voter_token,
            max_age=365 * 24 * 3600,
            httponly=True,
            samesite="Lax",
            # The token is the only credential behind the guest's votes: it
            # follows the session cookie, HTTPS-only unless the deployment
            # serves plain HTTP.
            secure=settings.SESSION_COOKIE_SECURE,
        )
        return response
