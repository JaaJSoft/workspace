from django.db import transaction

# One order for every member list, so the member panel and the mention
# autocomplete present people as they joined rather than as the database
# happens to return them.
MEMBER_ORDER = ("joined_at", "uuid")

# How many names a generated group title strings together.
TITLE_NAMES = 3


def user_conversation_ids(user):
    """Return conversation UUIDs where the user is an active member."""
    from ..models import ConversationMember

    return ConversationMember.objects.filter(
        user=user,
        left_at__isnull=True,
    ).values_list("conversation_id", flat=True)


def active_member_users(conversation_id):
    """The user rows that are active members of *conversation_id*.

    The pool a mention posted into that conversation may resolve against. An
    unnarrowed pool turns a rendered badge's data-user-id into a
    username -> id map for the whole workspace, which is a disclosure on any
    path whose author is not themselves a member.
    """
    from django.contrib.auth import get_user_model

    from ..models import ConversationMember

    return get_user_model().objects.filter(
        id__in=ConversationMember.objects.filter(
            conversation_id=conversation_id, left_at__isnull=True
        ).values("user_id")
    )


def active_members_queryset():
    """Active members with the user rows a serializer needs, in MEMBER_ORDER.

    The queryset every ``Prefetch("members", ...)`` should be built from, so a
    conversation carries the same member list wherever it is rendered.
    """
    from ..models import ConversationMember

    return (
        ConversationMember.objects.filter(left_at__isnull=True)
        .select_related("user", "user__bot_profile")
        .order_by(*MEMBER_ORDER)
    )


def dm_partners(user, conversation_ids):
    """The other participant of each direct message, keyed by conversation id.

    Members can only be added to a group (``ConversationMembersView`` rejects
    anything else), so a direct message holds exactly two of them and this
    reads one row per conversation - no ranking, no cap. Groups are absent on
    purpose: they are labelled from their title, so the sidebar never needs to
    know who is in one.
    """
    from ..models import Conversation, ConversationMember

    partners = (
        ConversationMember.objects.filter(
            conversation_id__in=conversation_ids,
            conversation__kind=Conversation.Kind.DM,
            left_at__isnull=True,
        )
        .exclude(user_id=user.id)
        .select_related("user")
    )
    return {m.conversation_id: m.user for m in partners}


def default_group_title(users):
    """The name a group falls back to when its creator supplies none.

    Strings together the first few members, which is what an unnamed group used
    to be *displayed* as. Storing it instead of recomputing it is what lets a
    group row cost nothing in member rows - at the price of a name that no
    longer follows the membership, and of listing the reader among the names.
    """
    names = [(u.get_full_name() or u.username) for u in users[:TITLE_NAMES]]
    return ", ".join(names) or "Group"


def display_name_for(kind, title, partner=None):
    """The name a conversation is labelled with, wherever it is rendered.

    The sidebar row and the voice room's heading are two renderings of the same
    label, so they read it from here rather than each deriving it from whatever
    they happen to have loaded - which is how the room ended up listing members
    under a conversation the sidebar called "Group".

    *partner* is only consulted for a direct message: a group is named by its
    title, and every group has one.
    """
    from ..models import Conversation

    if title:
        return title
    if kind == Conversation.Kind.DM:
        if partner:
            return partner.get_full_name() or partner.username
        return "Conversation"
    return "Group"


def backfill_group_titles(conversation_model, member_model):
    """Name the group conversations whose creator left the title blank.

    Called by migration 0030_name_untitled_groups, which passes the historical
    models from the app registry. Its signature therefore has to stay
    compatible, and the body must keep working against historical model
    classes: no model methods, no properties, only fields and the manager.

    A group used to be *displayed* as the names of its first members whenever
    it had no title, recomputed per reader and per request. The sidebar now
    reads the stored title, so rows predating that rule need one too. The name
    it generates is what the reader used to see, minus the exclusion of
    themselves - a stored title is the same for everyone.

    Idempotent: a conversation that already has a title is left alone.
    """
    untitled = list(
        conversation_model.objects.filter(kind="group", title="").only("uuid")
    )
    if not untitled:
        return 0

    names = {}
    members = (
        member_model.objects.filter(
            conversation_id__in=[c.uuid for c in untitled],
            left_at__isnull=True,
        )
        .select_related("user")
        .order_by(*MEMBER_ORDER)
    )
    for member in members:
        bucket = names.setdefault(member.conversation_id, [])
        if len(bucket) < TITLE_NAMES:
            user = member.user
            full_name = f"{user.first_name} {user.last_name}".strip()
            bucket.append(full_name or user.username)

    for conversation in untitled:
        conversation.title = ", ".join(names.get(conversation.uuid, [])) or "Group"
    conversation_model.objects.bulk_update(untitled, ["title"])
    return len(untitled)


def get_active_membership(user, conversation_id):
    """Return the active ConversationMember for *user* in *conversation_id*, or None."""
    from ..models import ConversationMember

    return ConversationMember.objects.filter(
        conversation_id=conversation_id,
        user=user,
        left_at__isnull=True,
    ).first()


def is_active_member(user_id, conversation_id):
    """Whether *user_id* (a raw id, not a User object) is an active member.

    Companion to ``get_active_membership`` for cases where only the id is known
    (e.g. validating the target of a relayed signal) so membership checks stay
    in one place.
    """
    from ..models import ConversationMember

    return ConversationMember.objects.filter(
        conversation_id=conversation_id,
        user_id=user_id,
        left_at__isnull=True,
    ).exists()


def is_bot_conversation(conversation_id):
    """Whether the conversation includes an AI bot member.

    Used to disable features that make no sense with a bot, such as calls.
    """
    from ..models import ConversationMember

    return ConversationMember.objects.filter(
        conversation_id=conversation_id,
        user__bot_profile__isnull=False,
    ).exists()


@transaction.atomic
def get_or_create_dm(user, other_user):
    """Get or create a DM conversation between two users.

    Deduplicates by finding an existing DM with exactly these two active members.
    If a member had left, reactivates them.
    """
    from ..models import Conversation, ConversationMember

    user_ids = sorted([user.id, other_user.id])

    # Find existing DM with both users as members
    existing = (
        Conversation.objects.filter(kind=Conversation.Kind.DM)
        .filter(
            members__user_id=user_ids[0],
        )
        .filter(
            members__user_id=user_ids[1],
        )
        .first()
    )

    if existing:
        # Reactivate any member that left
        ConversationMember.objects.filter(
            conversation=existing,
            user_id__in=user_ids,
            left_at__isnull=False,
        ).update(left_at=None)
        return existing

    conversation = Conversation.objects.create(
        kind=Conversation.Kind.DM,
        created_by=user,
    )
    ConversationMember.objects.bulk_create(
        [
            ConversationMember(conversation=conversation, user=user),
            ConversationMember(conversation=conversation, user=other_user),
        ]
    )
    return conversation


def get_unread_counts(user):
    """Return unread message counts for each conversation the user is in."""
    from ..models import ConversationMember

    memberships = ConversationMember.objects.filter(
        user=user,
        left_at__isnull=True,
        unread_count__gt=0,
    ).values_list("conversation_id", "unread_count")

    conversations = {}
    total = 0
    for conv_id, count in memberships:
        conversations[str(conv_id)] = count
        total += count

    return {"total": total, "conversations": conversations}
