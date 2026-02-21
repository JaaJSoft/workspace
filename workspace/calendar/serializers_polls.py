from rest_framework import serializers

from .models import Poll, PollInvitee, PollSlot, PollVote
from .serializers import MemberUserSerializer


class PollSlotSerializer(serializers.ModelSerializer):
    yes_count = serializers.SerializerMethodField()
    maybe_count = serializers.SerializerMethodField()

    class Meta:
        model = PollSlot
        fields = ['uuid', 'start', 'end', 'position', 'yes_count', 'maybe_count']

    def get_yes_count(self, obj):
        return obj.votes.filter(choice='yes').count()

    def get_maybe_count(self, obj):
        return obj.votes.filter(choice='maybe').count()


class PollVoteSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer(read_only=True)

    class Meta:
        model = PollVote
        fields = ['uuid', 'slot_id', 'user', 'guest_name', 'voter_token', 'choice']


class PollInviteeSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer(read_only=True)

    class Meta:
        model = PollInvitee
        fields = ['uuid', 'user', 'created_at']


class PollSerializer(serializers.ModelSerializer):
    created_by = MemberUserSerializer(read_only=True)
    slots = PollSlotSerializer(many=True, read_only=True)
    votes = serializers.SerializerMethodField()
    invitees = PollInviteeSerializer(many=True, read_only=True)
    share_url = serializers.SerializerMethodField()

    class Meta:
        model = Poll
        fields = [
            'uuid', 'title', 'description', 'created_by', 'status',
            'share_token', 'chosen_slot_id', 'event_id',
            'slots', 'votes', 'invitees', 'share_url',
            'created_at', 'updated_at',
        ]

    def get_votes(self, obj):
        votes = PollVote.objects.filter(
            slot__poll=obj,
        ).select_related('user')
        return PollVoteSerializer(votes, many=True).data

    def get_share_url(self, obj):
        request = self.context.get('request')
        path = f'/calendar/polls/shared/{obj.share_token}'
        if request:
            return request.build_absolute_uri(path)
        return path


class PollListSerializer(serializers.ModelSerializer):
    created_by = MemberUserSerializer(read_only=True)
    participant_count = serializers.SerializerMethodField()

    class Meta:
        model = Poll
        fields = ['uuid', 'title', 'status', 'created_by', 'participant_count', 'created_at']

    def get_participant_count(self, obj):
        return (
            PollVote.objects
            .filter(slot__poll=obj)
            .values('user', 'guest_name')
            .distinct()
            .count()
        )


class PollCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, default='', allow_blank=True)
    slots = serializers.ListField(
        child=serializers.DictField(),
        min_length=2,
    )

    def validate_slots(self, value):
        for i, slot in enumerate(value):
            if 'start' not in slot:
                raise serializers.ValidationError(f'Slot {i}: "start" is required.')
        return value


class PollUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)


class VoteItemSerializer(serializers.Serializer):
    slot_id = serializers.UUIDField()
    choice = serializers.ChoiceField(choices=PollVote.Choice.choices)


class VoteSubmitSerializer(serializers.Serializer):
    votes = VoteItemSerializer(many=True, min_length=1)


class GuestVoteSubmitSerializer(serializers.Serializer):
    guest_name = serializers.CharField(max_length=100)
    guest_email = serializers.EmailField(required=False, default='', allow_blank=True)
    voter_token = serializers.CharField(max_length=36, required=False, default='', allow_blank=True)
    votes = VoteItemSerializer(many=True, min_length=1)


class PollInviteSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)


class PollFinalizeSerializer(serializers.Serializer):
    slot_id = serializers.UUIDField()
