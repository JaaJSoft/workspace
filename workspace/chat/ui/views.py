from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import OuterRef, Prefetch, Subquery, prefetch_related_objects
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
    PinnedConversation,
    PinnedMessage,
)
from workspace.chat.serializers import ConversationListSerializer
from workspace.chat.services.avatar import avatar_initial_for
from workspace.chat.services.conversations import (
    active_members_queryset,
    display_name_for,
    dm_partners,
    get_active_membership,
    get_unread_counts,
    user_conversation_ids,
)
from workspace.chat.services.reactions import quick_reactions_for
from workspace.chat.services.threads import show_thread_replies_inline
from workspace.common.dates import time_ago
from workspace.common.uuids import parse_uuid_or_none
from workspace.files.ui.viewers import ViewerRegistry
from workspace.users.services.settings import get_setting


def _user_chat_groups(user):
    """Groups the user can attach conversations to, shaped for json_script."""
    return [{"id": g.pk, "name": g.name} for g in user.groups.order_by("name")]


def _display(user):
    """The name a person is shown under in a message preview."""
    return user.get_full_name() or user.username


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
        .select_related("author")
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
                c.last_message_preview = f"{_display(c._last_message.author)}: {body}"
            elif att := list(c._last_message.attachments.all()):
                label = "sent a file" if len(att) == 1 else f"sent {len(att)} files"
                c.last_message_preview = f"{_display(c._last_message.author)}: {label}"
            else:
                c.last_message_preview = f"{_display(c._last_message.author)}: "
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

    return render(
        request,
        "chat/ui/room.html",
        {
            "conversation_uuid": str(conversation_uuid),
            "conversation_title": title,
            "conversation": conversation_data,
            "ice_servers": settings.CHAT_CALL_ICE_SERVERS,
            "call_sounds_enabled": get_setting(
                request.user, "chat", "call_sounds", default=True
            ),
            "chat_prefs": _chat_prefs(request.user),
            "current_user_id": request.user.id,
            "chat_groups": _user_chat_groups(request.user),
            "voice_max_seconds": settings.CHAT_VOICE_MAX_SECONDS,
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


def group_messages(messages, current_user):
    """Group consecutive messages by same author within 5 min, with date separators.

    Returns a list of dicts:
      {'type': 'date', 'date': date_obj}
      {'type': 'messages', 'author': user, 'is_own': bool, 'messages': [msg, ...]}
    """
    groups = []
    current_date = None
    current_group = None

    for msg in messages:
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

        # Check if this message continues the current group
        can_group = (
            current_group
            and current_group["author"].id == msg.author_id
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
                "author": msg.author,
                "is_own": msg.author_id == current_user.id,
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
            "reply_to",
            "reply_to__author",
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

    groups = group_messages(messages_page, request.user)

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
            "current_user": request.user,
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
        .select_related("author", "author__bot_profile", "conversation")
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
            "reply_to",
            "reply_to__author",
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

    return render(
        request,
        "chat/ui/partials/thread_message_list.html",
        {
            "groups": group_messages(page, request.user),
            # The root renders outside the paginated list, on the first page
            # only: "load older" prepends into the list, so a root inside it
            # would sink below the older replies; an older page prepends into a
            # panel that already shows it.
            "root_groups": None
            if is_older_page
            else group_messages([root], request.user),
            "has_more": has_more,
            "first_uuid": str(page[0].uuid) if page else "",
            "root_uuid": root.uuid,
            "current_user": request.user,
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
