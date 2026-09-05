import logging
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import OuterRef, Prefetch, Subquery, prefetch_related_objects
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Meeting,
    Message,
    MessageAttachment,
    PinnedConversation,
    PinnedMessage,
)
from workspace.chat.serializers import ConversationListSerializer
from workspace.chat.services.avatar import avatar_initial_for
from workspace.chat.services.calls import is_call_locked
from workspace.chat.services.conversations import (
    active_members_queryset,
    display_name_for,
    dm_partners,
    get_active_membership,
    get_unread_counts,
    user_conversation_ids,
)
from workspace.chat.services.guest_messages import (
    hide_quotes_below_floor,
    message_queryset,
)
from workspace.chat.services.identities import display_name_for_identity
from workspace.chat.services.meeting_guests import guest_for_slug
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.reactions import quick_reactions_for
from workspace.chat.services.threads import show_thread_replies_inline
from workspace.chat.throttling import meeting_public_ip_limited
from workspace.common.dates import time_ago
from workspace.common.logging import scrub
from workspace.common.uuids import parse_uuid_or_none
from workspace.files.ui.viewers import ViewerRegistry
from workspace.users.services.settings import get_setting

from .viewer import for_guest, for_user, message_participant_key

logger = logging.getLogger(__name__)


def _user_chat_groups(user):
    """Groups the user can attach conversations to, shaped for json_script."""
    return [{"id": g.pk, "name": g.name} for g in user.groups.order_by("name")]


def _display(msg):
    """The name a person is shown under in a message preview."""
    return display_name_for_identity(msg.author, msg.guest)


# Entrance animation styles offered in the preferences panel; the values are
# the data-msg-animation variants chat.css knows.
MESSAGE_ANIMATIONS = [
    ("slide", "Slide"),
    ("pop", "Pop"),
    ("fade", "Fade"),
    ("bounce", "Bounce"),
    ("none", "None"),
]


def _chat_prefs(user):
    """Chat preferences shaped for json_script, guarded against non-dict values."""
    prefs = get_setting(user, "chat", "preferences", default={})
    return prefs if isinstance(prefs, dict) else {}


def _build_conversation_context(user, conversation_uuids=None, *, embed_members=False):
    """Build conversation list with display data for templates.

    ``conversation_uuids`` optionally restricts the build to a subset of the
    user's conversations (used by the per-item partial refresh). UUIDs the
    user is not an active member of are silently dropped by the membership
    filter, so callers can pass untrusted ids.

    ``embed_members`` loads every active member of every conversation, which
    only the page load needs - it serializes the list into the payload the
    Alpine app reads. The refresh paths label a group from its title and a
    direct message from its partner, so they read one row per direct message
    and nothing at all per group.
    """
    member_convos = user_conversation_ids(user)

    conversations = Conversation.objects.filter(uuid__in=member_convos)
    if conversation_uuids is not None:
        conversations = conversations.filter(uuid__in=conversation_uuids)
    if embed_members:
        conversations = conversations.prefetch_related(
            Prefetch("members", queryset=active_members_queryset()),
        )
    conversations = conversations.prefetch_related("groups").order_by("-updated_at")

    last_msg_subquery = (
        Message.objects.filter(
            conversation=OuterRef("pk"),
            deleted_at__isnull=True,
        )
        .order_by("-created_at")
        .values("uuid")[:1]
    )
    conversations = conversations.annotate(_last_msg_id=Subquery(last_msg_subquery))
    conv_list = list(conversations)
    partners = {} if embed_members else dm_partners(user, [c.uuid for c in conv_list])

    last_msg_ids = [c._last_msg_id for c in conv_list if c._last_msg_id]
    last_msgs = {
        m.uuid: m
        for m in Message.objects.filter(uuid__in=last_msg_ids)
        .select_related("author", "guest")
        .prefetch_related("attachments")
    }

    unread_data = get_unread_counts(user)
    unread_map = unread_data.get("conversations", {})

    # Build pin map: {conversation_uuid: position}
    pin_map = {
        str(p.conversation_id): p.position
        for p in PinnedConversation.objects.filter(owner=user)
    }

    now = timezone.now()
    for c in conv_list:
        c._last_message = last_msgs.get(c._last_msg_id)
        c.unread_count = unread_map.get(str(c.uuid), 0)

        # Pin data
        pin_pos = pin_map.get(str(c.uuid))
        c.is_pinned = pin_pos is not None
        c.pin_position = pin_pos if pin_pos is not None else None

        if embed_members:
            partner = next(
                (m.user for m in c.members.all() if m.user_id != user.id), None
            )
        else:
            partner = partners.get(c.uuid)

        c.display_name = display_name_for(c.kind, c.title, partner)
        c.avatar_initial = avatar_initial_for(c.kind, c.title, partner)
        c.other_user = partner if c.kind == Conversation.Kind.DM else None

        # Last message preview & time ago
        if c._last_message:
            body = c._last_message.body
            if body:
                if len(body) > 30:
                    body = body[:30] + "\u2026"
                c.last_message_preview = f"{_display(c._last_message)}: {body}"
            elif att := list(c._last_message.attachments.all()):
                label = "sent a file" if len(att) == 1 else f"sent {len(att)} files"
                c.last_message_preview = f"{_display(c._last_message)}: {label}"
            else:
                c.last_message_preview = f"{_display(c._last_message)}: "
            c.time_ago = time_ago(c._last_message.created_at, now=now)
        else:
            c.last_message_preview = "No messages yet"
            c.time_ago = ""

    return conv_list


@login_required
@ensure_csrf_cookie
def chat_view(request, conversation_uuid=None):
    """Main chat page with server-rendered conversation list."""
    conv_list = _build_conversation_context(request.user, embed_members=True)
    serializer = ConversationListSerializer(
        conv_list, many=True, context={"request": request}
    )

    pinned = sorted(
        [c for c in conv_list if c.is_pinned],
        key=lambda c: (c.pin_position or 0, c.created_at),
    )
    pinned_uuids = {str(c.uuid) for c in pinned}

    return render(
        request,
        "chat/ui/index.html",
        {
            "pinned_conversations": pinned,
            "other_conversations": [
                c for c in conv_list if str(c.uuid) not in pinned_uuids
            ],
            "conversations": serializer.data,
            "initial_conversation_uuid": str(conversation_uuid)
            if conversation_uuid
            else "",
            "ice_servers": settings.CHAT_CALL_ICE_SERVERS,
            "call_sounds_enabled": get_setting(
                request.user, "chat", "call_sounds", default=True
            ),
            "chat_prefs": _chat_prefs(request.user),
            "message_animations": MESSAGE_ANIMATIONS,
            "chat_groups": _user_chat_groups(request.user),
            "voice_max_seconds": settings.CHAT_VOICE_MAX_SECONDS,
        },
    )


@login_required
@ensure_csrf_cookie
def chat_room_view(request, conversation_uuid):
    """Dedicated voice-room page for a single conversation.

    Opens in its own browser tab and owns the WebRTC call. Access is gated by
    active membership, exactly like the message endpoints.
    """
    membership = get_active_membership(request.user, conversation_uuid)
    if not membership:
        return HttpResponseForbidden()

    # Reach the conversation through the authorized membership so authorization
    # and data retrieval stay tied together, then prefetch active members so the
    # serializer populates members/member_count/is_bot_conversation without N+1.
    conversation = membership.conversation
    prefetch_related_objects(
        [conversation],
        Prefetch("members", queryset=active_members_queryset()),
        "groups",
    )

    conversation_data = ConversationListSerializer(
        conversation, context={"request": request}
    ).data

    # Reuse the prefetched members so the heading matches the sidebar row.
    partner = next(
        (m.user for m in conversation.members.all() if m.user_id != request.user.id),
        None,
    )
    title = display_name_for(conversation.kind, conversation.title, partner)

    try:
        meeting = conversation.meeting
    except ObjectDoesNotExist:
        meeting = None
    meeting_data = None
    if meeting is not None:
        occurrence = current_occurrence(meeting)
        meeting_data = {
            "uuid": str(meeting.uuid),
            "slug": meeting.slug,
            "locked": is_call_locked(
                conversation.uuid, occurrence[0] if occurrence is not None else None
            ),
            "join_url": request.build_absolute_uri(meeting.join_path),
        }

    return render(
        request,
        "chat/ui/room.html",
        {
            "conversation_uuid": str(conversation_uuid),
            "conversation_title": title,
            "conversation": conversation_data,
            "meeting": meeting_data,
            "ice_servers": settings.CHAT_CALL_ICE_SERVERS,
            "call_sounds_enabled": get_setting(
                request.user, "chat", "call_sounds", default=True
            ),
            "chat_prefs": _chat_prefs(request.user),
            "message_animations": MESSAGE_ANIMATIONS,
            "current_user_id": request.user.id,
            "chat_groups": _user_chat_groups(request.user),
            "voice_max_seconds": settings.CHAT_VOICE_MAX_SECONDS,
        },
    )


@meeting_public_ip_limited
def meet_view(request, slug):
    """The public meeting page, reached from a bare /meet/<slug> link.

    Anonymous by construction, and the only view in this file that is: the
    page carries the slug and the ICE configuration, nothing else. Everything
    it shows is fetched at runtime with the token the visitor obtains by
    knocking, so a stranger loading this URL learns only that the slug exists
    - and the summary endpoint discloses that much anyway.
    """
    if not Meeting.objects.filter(slug=slug).exists():
        raise Http404
    return render(
        request,
        "chat/ui/meet.html",
        {"slug": slug, "ice_servers": settings.CHAT_CALL_ICE_SERVERS},
    )


@meeting_public_ip_limited
def meet_messages_view(request, slug):
    """Partial: the meeting's message list, rendered for an admitted guest.

    The second anonymous view in this file, and the reason the templates take
    a viewer: a guest loads the very same partial the member pane loads, so
    the two panes cannot drift into looking like different products. What a
    guest viewer changes is what is drawn - no control whose endpoint a guest
    has no right to call - and what is in scope: rows floored at the guest's
    own occurrence, no pinned markers, no bot indicator, and a
    data-conversation-uuid carrying the slug rather than the conversation id.

    Gated by the header token through ``guest_for_slug``, the same gate the
    JSON listing passes; a token that resolves to nothing, or to a guest of
    another meeting, is a 404 like everywhere else on the guest surface.
    """
    guest = guest_for_slug(request.headers.get("X-Meeting-Token", ""), slug)
    if guest is None:
        raise Http404

    conversation_id = guest.meeting.conversation_id
    # guest.occurrence_start is the value the gate already validated for this
    # token, never recomputed here: it is what keeps the conversation's
    # history from before the guest's occurrence out of reach.
    floor = guest.occurrence_start

    qs = message_queryset().filter(
        conversation_id=conversation_id, created_at__gte=floor
    )

    before = request.GET.get("before")
    if before:
        before_uuid = parse_uuid_or_none(before)
        if before_uuid is None:
            logger.debug("Ignoring malformed ?before cursor: %s", scrub(before))
        else:
            # Floored like the listing itself: a cursor naming a pre-window
            # message would otherwise read its created_at through the page
            # boundary it produces.
            cursor_msg = (
                Message.objects.filter(
                    conversation_id=conversation_id,
                    uuid=before_uuid,
                    created_at__gte=floor,
                )
                .only("created_at")
                .first()
            )
            if cursor_msg is None:
                logger.debug("Ignoring unknown ?before cursor: %s", scrub(before))
            else:
                qs = qs.filter(created_at__lt=cursor_msg.created_at)

    limit = 50
    messages_page = list(qs.order_by("-created_at")[: limit + 1])
    has_more = len(messages_page) > limit
    messages_page = messages_page[:limit]
    messages_page.reverse()
    hide_quotes_below_floor(messages_page, floor)

    conversation_kind = (
        Conversation.objects.filter(pk=conversation_id)
        .values_list("kind", flat=True)
        .first()
        or "dm"
    )

    viewer = for_guest(guest)

    return render(
        request,
        "chat/ui/partials/message_list.html",
        {
            "groups": group_messages(messages_page, viewer),
            "has_more": has_more,
            "first_uuid": str(messages_page[0].uuid) if messages_page else "",
            "viewer": viewer,
            "quick_emojis": [],
            "pinned_message_ids": set(),
            "conversation_kind": conversation_kind,
            # The slug, never the conversation id: the client only compares
            # this value for staleness, and a guest must not learn the id that
            # addresses the member-side conversation endpoints.
            "conversation_uuid": slug,
            "bot_processing": False,
        },
    )


@login_required
def conversation_list_view(request):
    """Partial: conversation list HTML for alpine-ajax refresh."""
    conv_list = _build_conversation_context(request.user)

    search = (request.GET.get("q") or "").strip().lower()
    if search:
        conv_list = [c for c in conv_list if search in c.display_name.lower()]

    pinned = sorted(
        [c for c in conv_list if c.is_pinned],
        key=lambda c: (c.pin_position or 0, c.created_at),
    )
    pinned_uuids = {str(c.uuid) for c in pinned}

    return render(
        request,
        "chat/ui/partials/conversation_list.html",
        {
            "pinned_conversations": pinned,
            "other_conversations": [
                c for c in conv_list if str(c.uuid) not in pinned_uuids
            ],
            "search_query": request.GET.get("q", ""),
        },
    )


@login_required
def conversation_items_view(request):
    """Partial: individual sidebar conversation rows for targeted swaps.

    Called with ``?uuids=<uuid>&uuids=<uuid>`` after a message is sent or
    received so alpine-ajax only re-renders the affected rows
    (id="conv-item-<uuid>") instead of the whole conversation list.
    """
    raw_uuids = request.GET.getlist("uuids")
    if not raw_uuids:
        return HttpResponse(status=400)

    uuids = [parse_uuid_or_none(raw) for raw in raw_uuids]
    if None in uuids:
        return HttpResponse(status=400)

    conv_list = _build_conversation_context(request.user, conversation_uuids=uuids)

    return render(
        request,
        "chat/ui/partials/_conversation_items.html",
        {"conversations": conv_list},
    )


def _group_author(msg):
    """The value the message-group template reads as `author`.

    A real author is passed through unchanged - a `User` has no `is_guest`,
    so templates read it as `author.is_guest|default:False`. A guest has no
    user row, so it gets a stand-in exposing exactly the surface the
    templates read off it - `.id`, `.username`, `.get_full_name()`,
    `.is_guest` - computed through the same resolver as everywhere else,
    rather than a second guest-formatting rule growing here.
    """
    if msg.author is not None:
        return msg.author
    display_name = display_name_for_identity(msg.author, msg.guest)
    return SimpleNamespace(
        id=None,
        username=display_name,
        get_full_name=lambda: display_name,
        is_guest=True,
    )


def group_messages(messages, viewer):
    """Group consecutive messages by same author within 5 min, with date separators.

    *viewer* is a ``ui.viewer.Viewer``: ``is_own`` compares participant keys
    rather than user ids, so a meeting guest reading the same list recognizes
    their own messages without a user row to compare against.

    Returns a list of dicts:
      {'type': 'date', 'date': date_obj}
      {'type': 'messages', 'author': user, 'is_own': bool, 'messages': [msg, ...]}

    Also stamps a guest-safe `quote_author_name` onto every replied-to
    message this batch touches (msg.reply_to), since this is the one place
    that already walks every message headed for the template - the reply
    quote otherwise has no other pass to piggyback on.
    """
    groups = []
    current_date = None
    current_group = None

    for msg in messages:
        # The reply quote renders a single name, never id/username - a plain
        # string sidesteps the template's {{ x|default:y }} filter-argument
        # trap entirely (see _group_author), rather than making the quote
        # walk a shim built for the group header's three-attribute needs.
        if msg.reply_to_id and msg.reply_to is not None:
            msg.reply_to.quote_author_name = display_name_for_identity(
                msg.reply_to.author, msg.reply_to.guest
            )

        msg_date = timezone.localdate(msg.created_at)

        # Insert date separator when the day changes
        if msg_date != current_date:
            if current_group:
                groups.append(current_group)
                current_group = None
            groups.append(
                {"type": "date", "date": msg_date, "datetime": msg.created_at}
            )
            current_date = msg_date

        # System messages (e.g. call start/end) never group with user messages
        # and render as their own centered row.
        if msg.kind == Message.Kind.SYSTEM:
            if current_group:
                groups.append(current_group)
                current_group = None
            groups.append({"type": "system", "message": msg})
            continue

        # Check if this message continues the current group. A guest has no
        # user row, so "same author" compares the (author_id, guest_id) pair
        # rather than dereferencing a possibly-None author.
        can_group = (
            current_group
            and current_group["author_id"] == msg.author_id
            and current_group["guest_id"] == msg.guest_id
            and not msg.deleted_at
            and not (current_group["messages"][-1].deleted_at)
            and (msg.created_at - current_group["messages"][-1].created_at)
            < timedelta(minutes=5)
        )

        if can_group:
            current_group["messages"].append(msg)
        else:
            if current_group:
                groups.append(current_group)
            current_group = {
                "type": "messages",
                "author_id": msg.author_id,
                "guest_id": msg.guest_id,
                "author": _group_author(msg),
                "is_own": message_participant_key(msg) == viewer.participant_key,
                "is_bot": hasattr(msg.author, "bot_profile"),
                "messages": [msg],
            }

    if current_group:
        groups.append(current_group)

    return groups


@login_required
def conversation_messages_view(request, conversation_uuid):
    """Partial: server-rendered grouped messages for a conversation."""
    membership = get_active_membership(request.user, conversation_uuid)
    if not membership:
        return HttpResponseForbidden()

    qs = (
        Message.objects.filter(conversation_id=conversation_uuid)
        .select_related(
            "author",
            "author__bot_profile",
            "guest",
            "reply_to",
            "reply_to__author",
            "reply_to__guest",
            "conversation",
            "interaction",
            "interaction__interacted_by",
        )
        .prefetch_related("reactions__user", "attachments", "link_previews__preview")
        .order_by("-created_at")
    )

    if not show_thread_replies_inline(request.user):
        qs = qs.filter(thread_root__isnull=True)

    before = request.GET.get("before")
    if before:
        before_uuid = parse_uuid_or_none(before)
        if before_uuid is not None:
            # Scope the cursor lookup to the current conversation: an unrestricted
            # Message.objects.get(uuid=...) would let a caller use a UUID from
            # another conversation as a cursor and read its created_at via the
            # resulting page boundary (cross-conversation timing oracle).
            cursor_msg = (
                Message.objects.filter(
                    conversation_id=conversation_uuid, uuid=before_uuid
                )
                .only("created_at")
                .first()
            )
            if cursor_msg is not None:
                qs = qs.filter(created_at__lt=cursor_msg.created_at)

    limit = 50
    messages_page = list(qs[: limit + 1])
    has_more = len(messages_page) > limit
    messages_page = messages_page[:limit]
    messages_page.reverse()  # Back to chronological order

    # Read receipt data: for own messages, compute read counts in bulk
    own_msgs = [
        m for m in messages_page if m.author_id == request.user.id and not m.deleted_at
    ]
    if own_msgs:
        member_read_ats = list(
            ConversationMember.objects.filter(
                conversation_id=conversation_uuid,
                left_at__isnull=True,
            )
            .exclude(user=request.user)
            .values_list("last_read_at", flat=True)
        )
        total_recipients = len(member_read_ats)
        for msg in own_msgs:
            read_count = sum(1 for ra in member_read_ats if ra and ra >= msg.created_at)
            msg.read_count = read_count
            msg.total_recipients = total_recipients
            msg.all_read = read_count == total_recipients and total_recipients > 0

    conversation_kind = (
        Conversation.objects.filter(
            pk=conversation_uuid,
        )
        .values_list("kind", flat=True)
        .first()
        or "dm"
    )

    viewer = for_user(request.user)
    groups = group_messages(messages_page, viewer)

    first_uuid = str(messages_page[0].uuid) if messages_page else ""

    pinned_message_ids = set(
        PinnedMessage.objects.filter(conversation_id=conversation_uuid).values_list(
            "message_id", flat=True
        )
    )

    # Check if there's an active AI task for this conversation
    from workspace.ai.models import AITask

    bot_processing = AITask.objects.filter(
        task_type=AITask.TaskType.CHAT,
        status__in=[AITask.Status.PENDING, AITask.Status.PROCESSING],
        input_data__conversation_id=str(conversation_uuid),
    ).exists()

    return render(
        request,
        "chat/ui/partials/message_list.html",
        {
            "groups": groups,
            "has_more": has_more,
            "first_uuid": first_uuid,
            "viewer": viewer,
            "quick_emojis": quick_reactions_for(request.user),
            "pinned_message_ids": pinned_message_ids,
            "conversation_kind": conversation_kind,
            "conversation_uuid": str(conversation_uuid),
            "bot_processing": bot_processing,
        },
    )


@login_required
def thread_messages_view(request, root_uuid):
    """Partial: a thread's root message followed by its replies."""
    root = (
        Message.objects.filter(uuid=root_uuid, thread_root__isnull=True)
        .select_related(
            "author",
            "author__bot_profile",
            "guest",
            "reply_to",
            "reply_to__author",
            "reply_to__guest",
            "conversation",
        )
        .first()
    )
    if root is None:
        raise Http404
    if not get_active_membership(request.user, root.conversation_id):
        return HttpResponseForbidden()

    # Live replies only: the counters on the root (recount_thread) count live
    # replies, and the panel must show the number the footer advertises.
    qs = (
        Message.objects.filter(thread_root=root, deleted_at__isnull=True)
        .select_related(
            "author",
            "author__bot_profile",
            "guest",
            "reply_to",
            "reply_to__author",
            "reply_to__guest",
            "conversation",
            "interaction",
            "interaction__interacted_by",
        )
        .prefetch_related("reactions__user", "attachments", "link_previews__preview")
        .order_by("-created_at")
    )

    # Set only when the cursor actually resolved. A malformed or unknown
    # `?before` is ignored, which makes the response the *first* page again -
    # and the first page has to carry the root.
    is_older_page = False

    before = request.GET.get("before")
    if before:
        before_uuid = parse_uuid_or_none(before)
        if before_uuid is not None:
            cursor = (
                Message.objects.filter(thread_root=root, uuid=before_uuid)
                .only("created_at")
                .first()
            )
            if cursor is not None:
                qs = qs.filter(created_at__lt=cursor.created_at)
                is_older_page = True

    limit = 50
    page = list(qs[: limit + 1])
    has_more = len(page) > limit
    page = page[:limit]
    page.reverse()

    pinned_message_ids = set(
        PinnedMessage.objects.filter(conversation_id=root.conversation_id).values_list(
            "message_id", flat=True
        )
    )

    viewer = for_user(request.user)

    return render(
        request,
        "chat/ui/partials/thread_message_list.html",
        {
            "groups": group_messages(page, viewer),
            # The root renders outside the paginated list, on the first page
            # only: "load older" prepends into the list, so a root inside it
            # would sink below the older replies; an older page prepends into a
            # panel that already shows it.
            "root_groups": None if is_older_page else group_messages([root], viewer),
            "has_more": has_more,
            "first_uuid": str(page[0].uuid) if page else "",
            "root_uuid": root.uuid,
            "viewer": viewer,
            "quick_emojis": quick_reactions_for(request.user),
            "pinned_message_ids": pinned_message_ids,
            "conversation_kind": root.conversation.kind,
            "conversation_uuid": root.conversation_id,
        },
    )


@login_required
def message_readers_view(request, conversation_uuid, message_uuid):
    """Partial: server-rendered popover content showing who read a message."""
    membership = get_active_membership(request.user, conversation_uuid)
    if not membership:
        return HttpResponseForbidden()

    try:
        message = Message.objects.get(
            uuid=message_uuid,
            conversation_id=conversation_uuid,
            deleted_at__isnull=True,
        )
    except Message.DoesNotExist:
        return HttpResponseForbidden()

    members = (
        ConversationMember.objects.filter(
            conversation_id=conversation_uuid,
            left_at__isnull=True,
        )
        .exclude(user=message.author)
        .select_related("user")
    )

    readers = []
    not_read = []
    for m in members:
        if m.last_read_at and m.last_read_at >= message.created_at:
            readers.append({"user": m.user, "read_at": m.last_read_at})
        else:
            not_read.append({"user": m.user})

    return render(
        request,
        "chat/ui/partials/_read_receipt_popover.html",
        {
            "readers": readers,
            "not_read": not_read,
            "message_uuid": message_uuid,
        },
    )


@login_required
def view_attachment(request, attachment_uuid):
    """Render viewer HTML for a chat attachment (read-only)."""
    attachment = (
        MessageAttachment.objects.select_related("message__conversation")
        .filter(
            uuid=attachment_uuid,
        )
        .first()
    )

    if not attachment:
        from django.http import Http404

        raise Http404

    # Check user is member of the conversation
    if not get_active_membership(request.user, attachment.message.conversation_id):
        from django.http import Http404

        raise Http404

    from workspace.files.services.filetype import get_viewer_by_slug
    from workspace.files.ui.viewers import render_viewer_panel

    # A pinned viewer wins; an unknown pin degrades to content-based
    # resolution rather than breaking the modal.
    ViewerClass = get_viewer_by_slug(attachment.viewer) or ViewerRegistry.get_viewer(
        attachment.type, attachment.original_name
    )
    if not ViewerClass:
        return HttpResponse(
            render_viewer_panel(
                f'<div class="p-8 text-center text-error">No viewer available for {attachment.type}</div>'
            ),
            status=400,
        )

    class AttachmentAdapter:
        def __init__(self, att):
            self.uuid = att.uuid
            self.name = att.original_name
            self.mime_type = att.mime_type
            self.type = att.type
            self.category = att.category
            self.content = att.file

        def is_viewable(self):
            return True

    adapter = AttachmentAdapter(attachment)
    viewer = ViewerClass(adapter)
    viewer._user_can_edit = False
    viewer._content_url = f"/api/v1/chat/attachments/{attachment.uuid}"
    html = viewer.render(request)

    return HttpResponse(render_viewer_panel(html))
