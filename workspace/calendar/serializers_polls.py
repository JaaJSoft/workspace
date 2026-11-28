from django.urls import reverse
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from workspace.common.serializers import ApiModelSerializer
from workspace.users.serializers import UserSummarySerializer

from .models import Poll, PollSlot, PollVote


def _votes(poll):
    """The votes of *poll*: the ones ``services.polls.attach_votes`` gave it,
    or read now for a poll serialized on its own."""
    if not hasattr(poll, "cast_votes"):
        poll.cast_votes = list(
            PollVote.objects.filter(slot__poll=poll)
            .select_related("user")
            .order_by("created_at", "uuid")
        )
    return poll.cast_votes


class PollSlotSerializer(ApiModelSerializer):
    yes_count = serializers.IntegerField(read_only=True, default=0)
    maybe_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = PollSlot
        fields = ["uuid", "start", "end", "position", "yes_count", "maybe_count"]


class PollVoteSerializer(ApiModelSerializer):
    """A vote as anyone who can open the poll sees it.

    A guest's ``voter_token`` is the only credential behind their votes and
    ``guest_email`` is theirs alone, so neither is ever serialized here:
    ``is_mine`` is how a caller recognizes its own votes.
    """

    slot = serializers.UUIDField(source="slot_id")
    user = UserSummarySerializer(read_only=True, allow_null=True)
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = PollVote
        fields = ["uuid", "slot", "user", "guest_name", "choice", "is_mine"]

    def get_is_mine(self, vote) -> bool:
        if vote.user_id is not None:
            request = self.context.get("request")
            return request is not None and vote.user_id == request.user.id
        voter_token = self.context.get("voter_token")
        return bool(voter_token) and vote.voter_token == voter_token


class PollEventSerializer(serializers.Serializer):
    uuid = serializers.UUIDField()


class PollSerializer(ApiModelSerializer):
    created_by = UserSummarySerializer(read_only=True)
    slots = PollSlotSerializer(many=True, read_only=True)
    chosen_slot = serializers.UUIDField(
        source="chosen_slot_id",
        read_only=True,
        allow_null=True,
        help_text="The uuid of the slot the creator chose; null while the poll is open.",
    )
    event = serializers.SerializerMethodField()
    votes = serializers.SerializerMethodField()
    invitees = serializers.SerializerMethodField()
    participant_count = serializers.SerializerMethodField()
    share_url = serializers.SerializerMethodField()

    class Meta:
        model = Poll
        fields = [
            "uuid",
            "title",
            "description",
            "created_by",
            "status",
            "slots",
            "chosen_slot",
            "event",
            "votes",
            "invitees",
            "participant_count",
            "share_url",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(PollEventSerializer(allow_null=True))
    def get_event(self, poll):
        """The event the chosen time became."""
        return {"uuid": str(poll.event_id)} if poll.event_id else None

    @extend_schema_field(PollVoteSerializer(many=True))
    def get_votes(self, poll):
        return PollVoteSerializer(_votes(poll), many=True, context=self.context).data

    @extend_schema_field(UserSummarySerializer(many=True))
    def get_invitees(self, poll):
        users = [invitee.user for invitee in poll.invitees.all()]
        return UserSummarySerializer(users, many=True, context=self.context).data

    def get_participant_count(self, poll) -> int:
        """How many people voted: accounts, and guests of the share link."""
        votes = _votes(poll)
        accounts = {vote.user_id for vote in votes if vote.user_id is not None}
        guests = {
            vote.voter_token
            for vote in votes
            if vote.user_id is None and vote.voter_token
        }
        return len(accounts) + len(guests)

    def get_share_url(self, poll) -> str:
        """The page of the poll for people without an account."""
        path = reverse("calendar_ui:polls-shared", args=[poll.share_token])
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path


def public_display_name(user):
    """The name a workspace user goes by on a page anyone with the link can open."""
    return user.get_full_name() or user.username


def _voter_identity(vote):
    if vote.user_id is not None:
        return ("user", vote.user_id)
    return ("guest", vote.voter_token or vote.guest_name)


class PublicPollVoteSerializer(ApiModelSerializer):
    """A vote as an anonymous visitor of the share link sees it.

    The voter is reduced to a display name: no user id, username or email.
    ``participant`` groups one voter's votes into a row; it is an ordinal
    assigned per payload (``participant_keys`` in the context), so it says
    nothing about who the voter is in the workspace.
    """

    slot = serializers.UUIDField(source="slot_id")
    name = serializers.SerializerMethodField()
    participant = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = PollVote
        fields = ["uuid", "slot", "participant", "name", "choice", "is_mine"]

    def get_name(self, vote) -> str:
        if vote.user_id is not None:
            return public_display_name(vote.user)
        return vote.guest_name

    def get_participant(self, vote) -> str:
        return self.context["participant_keys"][_voter_identity(vote)]

    def get_is_mine(self, vote) -> bool:
        voter_token = self.context.get("voter_token")
        return (
            vote.user_id is None
            and bool(voter_token)
            and vote.voter_token == voter_token
        )


class PublicPollSerializer(ApiModelSerializer):
    """A poll as served on its public share link, to visitors who may not have an account.

    Unlike ``PollSerializer`` it carries no invitee list and names workspace
    users by display name only. ``voter_token`` in the context is the guest
    the browser votes as: their votes are flagged ``is_mine``, and the name
    and email they gave come back to them alone.
    """

    created_by = serializers.SerializerMethodField()
    slots = PollSlotSerializer(many=True, read_only=True)
    chosen_slot = serializers.UUIDField(
        source="chosen_slot_id", read_only=True, allow_null=True
    )
    votes = serializers.SerializerMethodField()
    my_guest_name = serializers.SerializerMethodField()
    my_guest_email = serializers.SerializerMethodField()

    class Meta:
        model = Poll
        fields = [
            "uuid",
            "title",
            "description",
            "created_by",
            "status",
            "slots",
            "chosen_slot",
            "votes",
            "my_guest_name",
            "my_guest_email",
        ]

    def _mine(self, poll):
        voter_token = self.context.get("voter_token")
        if not voter_token:
            return None
        return next(
            (
                vote
                for vote in _votes(poll)
                if vote.user_id is None and vote.voter_token == voter_token
            ),
            None,
        )

    def get_created_by(self, poll) -> str:
        return public_display_name(poll.created_by)

    @extend_schema_field(PublicPollVoteSerializer(many=True))
    def get_votes(self, poll):
        votes = _votes(poll)
        identities = dict.fromkeys(_voter_identity(vote) for vote in votes)
        participant_keys = {
            identity: f"p{index}" for index, identity in enumerate(identities, start=1)
        }
        context = {**self.context, "participant_keys": participant_keys}
        return PublicPollVoteSerializer(votes, many=True, context=context).data

    def get_my_guest_name(self, poll) -> str:
        mine = self._mine(poll)
        return mine.guest_name if mine else ""

    def get_my_guest_email(self, poll) -> str:
        mine = self._mine(poll)
        return mine.guest_email if mine else ""


class PollSlotInputSerializer(serializers.Serializer):
    """A proposed time. ``uuid`` names a slot the poll already has, on update."""

    uuid = serializers.UUIDField(required=False, allow_null=True)
    start = serializers.DateTimeField()
    end = serializers.DateTimeField(required=False, allow_null=True)


class PollWriteSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    slots = PollSlotInputSerializer(
        many=True,
        min_length=2,
        help_text=(
            "The times proposed: the whole list. One naming a slot by its "
            "`uuid` keeps that slot and its votes; a slot left out is "
            "removed, votes included."
        ),
    )
    invitees = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text=(
            "The ids of the accounts invited to vote: the whole list. "
            "Someone no longer on it loses the votes they cast."
        ),
    )


class VoteItemSerializer(serializers.Serializer):
    slot = serializers.UUIDField()
    choice = serializers.ChoiceField(choices=PollVote.Choice.choices)


class VoteSubmitSerializer(serializers.Serializer):
    votes = VoteItemSerializer(many=True, min_length=1)


class GuestVoteSubmitSerializer(serializers.Serializer):
    guest_name = serializers.CharField(max_length=100)
    guest_email = serializers.EmailField(required=False, default="", allow_blank=True)
    votes = VoteItemSerializer(many=True, min_length=1)


class PollFinalizeSerializer(serializers.Serializer):
    slot = serializers.UUIDField()
