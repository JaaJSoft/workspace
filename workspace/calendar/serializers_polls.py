from rest_framework import serializers

from .models import Poll, PollInvitee, PollSlot, PollVote
from .serializers import MemberUserSerializer


class PollSlotSerializer(serializers.ModelSerializer):
    yes_count = serializers.IntegerField(read_only=True, default=0)
    maybe_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = PollSlot
        fields = ["uuid", "start", "end", "position", "yes_count", "maybe_count"]


class PollVoteSerializer(serializers.ModelSerializer):
    """A vote as anyone who can open the poll sees it.

    A guest's ``voter_token`` is the only credential behind their votes and
    ``guest_email`` is theirs alone, so neither is ever serialized here:
    ``is_mine`` is how a caller recognizes its own votes.
    """

    user = MemberUserSerializer(read_only=True)
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = PollVote
        fields = ["uuid", "slot_id", "user", "guest_name", "choice", "is_mine"]

    def get_is_mine(self, vote):
        if vote.user_id is not None:
            request = self.context.get("request")
            return request is not None and vote.user_id == request.user.id
        voter_token = self.context.get("voter_token")
        return bool(voter_token) and vote.voter_token == voter_token


class PollInviteeSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer(read_only=True)

    class Meta:
        model = PollInvitee
        fields = ["uuid", "user", "created_at"]


class PollSerializer(serializers.ModelSerializer):
    created_by = MemberUserSerializer(read_only=True)
    slots = PollSlotSerializer(many=True, read_only=True)
    votes = serializers.SerializerMethodField()
    invitees = PollInviteeSerializer(many=True, read_only=True)
    share_url = serializers.SerializerMethodField()

    class Meta:
        model = Poll
        fields = [
            "uuid",
            "title",
            "description",
            "created_by",
            "status",
            "share_token",
            "chosen_slot_id",
            "event_id",
            "slots",
            "votes",
            "invitees",
            "share_url",
            "created_at",
            "updated_at",
        ]

    def get_votes(self, obj):
        if hasattr(obj, "_prefetched_poll_votes"):
            votes = obj._prefetched_poll_votes
        else:
            votes = PollVote.objects.filter(slot__poll=obj).select_related("user")
        return PollVoteSerializer(votes, many=True, context=self.context).data

    def get_share_url(self, obj):
        request = self.context.get("request")
        path = f"/calendar/polls/shared/{obj.share_token}"
        if request:
            return request.build_absolute_uri(path)
        return path


def public_display_name(user):
    """The name a workspace user goes by on a page anyone with the link can open."""
    return user.get_full_name() or user.username


class PublicPollVoteSerializer(serializers.ModelSerializer):
    """A vote as an anonymous visitor of the share link sees it.

    The voter is reduced to a display name: no user id, username or email.
    ``participant`` groups one voter's votes into a row; it is an ordinal
    assigned per payload (``participant_keys`` in the context), so it says
    nothing about who the voter is in the workspace.
    """

    name = serializers.SerializerMethodField()
    participant = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = PollVote
        fields = ["uuid", "slot_id", "participant", "name", "choice", "is_mine"]

    def get_name(self, vote):
        if vote.user_id is not None:
            return public_display_name(vote.user)
        return vote.guest_name

    def get_participant(self, vote):
        return self.context["participant_keys"][_voter_identity(vote)]

    def get_is_mine(self, vote):
        voter_token = self.context.get("voter_token")
        return (
            vote.user_id is None
            and bool(voter_token)
            and vote.voter_token == voter_token
        )


def _voter_identity(vote):
    if vote.user_id is not None:
        return ("user", vote.user_id)
    return ("guest", vote.voter_token or vote.guest_name)


class PublicPollSerializer(serializers.ModelSerializer):
    """A poll as served on its public share link, to visitors who may not have an account.

    Unlike ``PollSerializer`` it carries no invitee list and names workspace
    users by display name only.
    """

    created_by = serializers.SerializerMethodField()
    slots = PollSlotSerializer(many=True, read_only=True)
    votes = serializers.SerializerMethodField()

    class Meta:
        model = Poll
        fields = [
            "uuid",
            "title",
            "description",
            "created_by",
            "status",
            "chosen_slot_id",
            "slots",
            "votes",
        ]

    def get_created_by(self, obj):
        return public_display_name(obj.created_by)

    def get_votes(self, obj):
        votes = obj._prefetched_poll_votes
        identities = dict.fromkeys(_voter_identity(vote) for vote in votes)
        participant_keys = {
            identity: f"p{index}" for index, identity in enumerate(identities, start=1)
        }
        context = {**self.context, "participant_keys": participant_keys}
        return PublicPollVoteSerializer(votes, many=True, context=context).data


class PollListSerializer(serializers.ModelSerializer):
    created_by = MemberUserSerializer(read_only=True)
    participant_count = serializers.SerializerMethodField()

    class Meta:
        model = Poll
        fields = [
            "uuid",
            "title",
            "status",
            "created_by",
            "participant_count",
            "created_at",
        ]

    def get_participant_count(self, obj):
        if hasattr(obj, "_participant_count"):
            return obj._participant_count
        # Fallback: distinct authenticated users + distinct guest tokens
        auth_count = (
            PollVote.objects.filter(slot__poll=obj, user__isnull=False)
            .values("user")
            .distinct()
            .count()
        )
        guest_count = (
            PollVote.objects.filter(slot__poll=obj, user__isnull=True)
            .exclude(voter_token="")
            .values("voter_token")
            .distinct()
            .count()
        )
        return auth_count + guest_count


class PollCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, default="", allow_blank=True)
    slots = serializers.ListField(
        child=serializers.DictField(),
        min_length=2,
    )

    def validate_slots(self, value):
        for i, slot in enumerate(value):
            if "start" not in slot:
                raise serializers.ValidationError(f'Slot {i}: "start" is required.')
        return value


class PollUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    slots = serializers.ListField(
        child=serializers.DictField(),
        min_length=2,
        required=False,
    )

    def validate_slots(self, value):
        for i, slot in enumerate(value):
            if "start" not in slot:
                raise serializers.ValidationError(f'Slot {i}: "start" is required.')
            if "uuid" in slot and slot["uuid"]:
                try:
                    import uuid as _uuid

                    _uuid.UUID(str(slot["uuid"]))
                except ValueError:
                    raise serializers.ValidationError(
                        f"Slot {i}: invalid uuid."
                    ) from None
        return value


class VoteItemSerializer(serializers.Serializer):
    slot_id = serializers.UUIDField()
    choice = serializers.ChoiceField(choices=PollVote.Choice.choices)


class VoteSubmitSerializer(serializers.Serializer):
    votes = VoteItemSerializer(many=True, min_length=1)


class GuestVoteSubmitSerializer(serializers.Serializer):
    guest_name = serializers.CharField(max_length=100)
    guest_email = serializers.EmailField(required=False, default="", allow_blank=True)
    votes = VoteItemSerializer(many=True, min_length=1)


class PollInviteSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)


class PollFinalizeSerializer(serializers.Serializer):
    slot_id = serializers.UUIDField()
