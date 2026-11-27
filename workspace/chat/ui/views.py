from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import OuterRef, Prefetch, Subquery, prefetch_related_objects
from django.db.models.functions import Lower
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext, ngettext, pgettext_lazy
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
from workspace.chat.services.calls import system_call_label
from workspace.chat.services.conversations import (
    active_members_queryset,
    display_name_for,
    dm_partners,
    get_active_membership,
    get_unread_counts,
    user_conversation_ids,
)
from workspace.chat.services.message_lists import find_message, page_messages
from workspace.chat.services.reactions import quick_reactions_for
from workspace.chat.services.threads import show_thread_replies_inline
from workspace.common.dates import time_ago
from workspace.common.prefetch import prefetchable
from workspace.common.services.image import SOURCE_TYPES
from workspace.common.uuids import parse_uuid_or_none
from workspace.users.queries import nameable_groups
from workspace.users.services.settings import get_setting


def _chat_groups(user):
    """Every group a conversation can follow, the user's own first, shaped for
    json_script. A conversation needs one of the user's own (group_sync), and
    can only follow the groups the user may name."""
    mine = set(user.groups.values_list("pk", flat=True))
    groups = [
        {"id": pk, "name": name, "mine": pk in mine}
        for pk, name in nameable_groups(user)
        .order_by(Lower("name"))
        .values_list("pk", "name")
    ]
    return sorted(groups, key=lambda group: not group["mine"])


def _display(user):
    """The name a person is shown under in a message preview."""
    return user.get_full_name() or user.username


def _last_message_preview(message):
    """The "Alice: hello" line under a conversation in the sidebar."""
    if message.kind == Message.Kind.SYSTEM and isinstance(message.tool_data, dict):
        text = system_call_label(message.tool_data)
    elif body := message.body:
        text = body[:30] + "\u2026" if len(body) > 30 else body
    elif count := len(message.attachments.all()):
        text = ngettext("sent a file", "sent %(count)s files", count) % {"count": count}
    else:
        text = ""
    # Translators: a conversation's last message in the sidebar, "Alice: hello".
    return gettext("%(author)s: %(text)s") % {
        "author": _display(message.author),
        "text": text,
    }


# Entrance animation styles offered in the preferences panel; the values are
# the data-msg-animation variants chat.css knows.
MESSAGE_ANIMATIONS = [
    ("slide", pgettext_lazy("message animation", "Slide")),
    ("pop", pgettext_lazy("message animation", "Pop")),
    ("fade", pgettext_lazy("message animation", "Fade")),
    ("bounce", pgettext_lazy("message animation", "Bounce")),
    ("none", pgettext_lazy("message animation", "None")),
]


def _chat_prefs(user):
    """Chat preferences shaped for json_script, guarded against non-dict values."""
    prefs = get_setting(user, "chat", "preferences", default={})
    return prefs if isinstance(prefs, dict) else {}


def _notification_levels():
    """Each ConversationMember notification level with its label and icon."""
    icons = ConversationMember.NOTIFICATION_LEVEL_ICONS
    return [
        {"value": level.value, "label": str(level.label), "icon": icons[level]}
        for level in ConversationMember.NotificationLevel
    ]


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
            c.last_message_preview = _last_message_preview(c._last_message)
            c.time_ago = time_ago(c._last_message.created_at, now=now)
        else:
            c.last_message_preview = gettext("No messages yet")
            c.time_ago = ""

    return conv_list


@prefetchable
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
            "chat_groups": _chat_groups(request.user),
            "voice_max_seconds": settings.CHAT_VOICE_MAX_SECONDS,
            "notification_levels": _notification_levels(),
            "group_avatar_types": SOURCE_TYPES,
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

    return render(
        request,
        "chat/ui/room.html",
        {
            "conversation_uuid": str(conversation_uuid),
            "conversation": conversation_data,
            "ice_servers": settings.CHAT_CALL_ICE_SERVERS,
            "call_sounds_enabled": get_setting(
                request.user, "chat", "call_sounds", default=True
            ),
            "chat_prefs": _chat_prefs(request.user),
            "message_animations": MESSAGE_ANIMATIONS,
            "current_user_id": request.user.id,
            "chat_groups": _chat_groups(request.user),
            "voice_max_seconds": settings.CHAT_VOICE_MAX_SECONDS,
            "notification_levels": _notification_levels(),
            "group_avatar_types": SOURCE_TYPES,
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
            previous = current_group or (groups[-1] if groups else None)
            current_group = {
                "type": "messages",
                "author": msg.author,
                "is_own": msg.author_id == current_user.id,
                "is_bot": hasattr(msg.author, "bot_profile"),
                # Same author right above, only split by the time window:
                # the name and avatar would say twice who is talking.
                "continues": bool(
                    previous
                    and previous["type"] == "messages"
                    and previous["author"].id == msg.author_id
                ),
                "messages": [msg],
            }

    if current_group:
        groups.append(current_group)

    return groups


def _ai_usage_prefetch():
    """The completed AI task behind each bot message, without its JSON payloads."""
    from workspace.ai.models import AITask

    return Prefetch(
        "ai_tasks",
        queryset=AITask.objects.filter(status=AITask.Status.COMPLETED)
        .only(
            "uuid",
            "chat_message_id",
            "status",
            "model_used",
            "prompt_tokens",
            "completion_tokens",
            "generation_seconds",
            "answer_tokens",
            "answer_seconds",
        )
        .order_by("-created_at"),
    )


def _window(paginator, page):
    """Where a page sits in its list, for the state element of the partial."""
    return {
        "offset": paginator.offset,
        "end": paginator.offset + len(page),
        "has_newer": paginator.has_more,
    }


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
        .prefetch_related(
            "reactions__user",
            "attachments",
            "link_previews__preview",
            _ai_usage_prefetch(),
        )
    )

    inline = show_thread_replies_inline(request.user)
    if not inline:
        qs = qs.filter(thread_root__isnull=True)

    # ?message= is looked up in this conversation only: a message of another
    # one opens the latest page, exactly like a message that does not exist.
    scope = Message.objects.filter(conversation_id=conversation_uuid)
    target = find_message(scope, request.GET.get("message"))
    anchor = target
    # A reply outside the flow is reached through its thread: the flow opens
    # on the root, and the client opens the panel on the reply.
    if target is not None and target.thread_root_id and not inline:
        anchor = find_message(scope, target.thread_root_id)
    messages_page, paginator = page_messages(request, qs, anchor)

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

    pinned_message_ids = set(
        PinnedMessage.objects.filter(conversation_id=conversation_uuid).values_list(
            "message_id", flat=True
        )
    )

    from workspace.ai.services.chat_generations import active_chat_tasks

    bot_processing = (
        active_chat_tasks()
        .filter(
            input_data__conversation_id=str(conversation_uuid),
        )
        .exists()
    )

    return render(
        request,
        "chat/ui/partials/message_list.html",
        {
            "groups": groups,
            "window": _window(paginator, messages_page),
            "target": target,
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
    # Scoped to the caller's conversations so a root elsewhere answers the
    # same 404 as one that does not exist.
    root = (
        Message.objects.filter(
            uuid=root_uuid,
            thread_root__isnull=True,
            conversation_id__in=user_conversation_ids(request.user),
        )
        .select_related("author", "author__bot_profile", "conversation")
        .prefetch_related(_ai_usage_prefetch())
        .first()
    )
    if root is None:
        raise Http404

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
        .prefetch_related(
            "reactions__user",
            "attachments",
            "link_previews__preview",
            _ai_usage_prefetch(),
        )
    )

    target = find_message(
        Message.objects.filter(thread_root=root, deleted_at__isnull=True),
        request.GET.get("message"),
    )
    page, paginator = page_messages(request, qs, target)

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
            # Outside the paginated list: an older or newer page merges into
            # the list's items, so a root inside them would sink below the
            # older replies. Those loads do not target it.
            "root_groups": group_messages([root], request.user),
            "window": _window(paginator, page),
            "target": target,
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
    from workspace.files.ui.viewers import (
        AttachmentItem,
        render_viewer_panel,
        viewer_class_for,
    )

    attachment = (
        MessageAttachment.objects.select_related("message__conversation")
        .filter(
            uuid=attachment_uuid,
        )
        .first()
    )

    if not attachment:
        raise Http404

    # Check user is member of the conversation
    if not get_active_membership(request.user, attachment.message.conversation_id):
        raise Http404

    item = AttachmentItem(attachment)
    # A pinned viewer wins; an unknown pin degrades to content-based
    # resolution rather than breaking the modal.
    ViewerClass = viewer_class_for(item)
    if not ViewerClass:
        return HttpResponse(
            render_viewer_panel(
                f'<div class="p-8 text-center text-error">No viewer available for {attachment.type}</div>',
                request,
            ),
            status=400,
        )

    viewer = ViewerClass(item)
    viewer._user_can_edit = False
    viewer._content_url = f"/api/v1/chat/attachments/{attachment.uuid}"
    html = viewer.render(request)

    return HttpResponse(render_viewer_panel(html, request))


@login_required
def attachment_previews(request):
    """A batch of <file-preview> slots for chat attachments.

    Same contract as Files' (``?uuids=...&targets=...``): an attachment of a
    conversation the user is not an active member of comes back as nothing.
    """
    from django.http import HttpResponseBadRequest

    from workspace.files.ui.viewers import (
        AttachmentItem,
        preview_targets,
        previews_response,
        render_file_preview,
    )

    pairs = preview_targets(request)
    if pairs is None:
        return HttpResponseBadRequest()
    attachments = {
        attachment.uuid: attachment
        for attachment in MessageAttachment.objects.filter(
            uuid__in={uuid for _, uuid in pairs},
            message__conversation_id__in=user_conversation_ids(request.user),
        )
    }
    rendered = {}
    for target, attachment_uuid in pairs:
        attachment = attachments.get(attachment_uuid)
        if attachment is None:
            continue
        rendered[target] = render_file_preview(
            request,
            AttachmentItem(attachment),
            content_url=f"/api/v1/chat/attachments/{attachment.uuid}",
        )
    return previews_response(rendered)
