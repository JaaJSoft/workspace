from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from workspace.common.cache import cached, invalidate_tags
from workspace.core.module_registry import registry
from workspace.core.sse_registry import notify_sse

from ..models import Notification
from ..tasks import send_push_notification

_UNREAD_TTL = 300  # 5 minutes
_PRIORITY_RANK = {"low": 0, "normal": 1, "high": 2, "urgent": 3}


def _user_tag(user_id):
    return f"notif:user:{user_id}"


# Model label -> Notification FK field. Most FK targets are the containers
# users open (conversation, not message) and double as dedup key and
# auto-read trigger; a mail message is the exception, since the message
# itself is the unit the user opens.
SOURCE_FIELDS = {
    "chat.conversation": "conversation",
    "files.file": "file",
    "projects.task": "task",
    "calendar.event": "event",
    "calendar.poll": "poll",
    "mail.mailmessage": "mail_message",
}


def source_field(source):
    """FK field name on Notification for *source*; ValueError if unmapped."""
    label = source._meta.label_lower
    try:
        return SOURCE_FIELDS[label]
    except KeyError:
        raise ValueError(
            f"{label} is not a notification source; add it to SOURCE_FIELDS"
        ) from None


def _resolve_module_defaults(origin, icon, color):
    """Fill icon/color from the module registry when not explicitly provided."""
    module = registry.get(origin)
    if module:
        if not icon:
            icon = module.icon
        if not color:
            color = module.color
    return icon, color


def notify(
    *,
    recipient,
    origin,
    icon="",
    title,
    body="",
    url="",
    actor=None,
    priority="normal",
    color="",
    source=None,
):
    """Create a single notification and trigger SSE push."""
    icon, color = _resolve_module_defaults(origin, icon, color)
    source_kwargs = {source_field(source): source} if source is not None else {}
    notif = Notification.objects.create(
        recipient=recipient,
        origin=origin,
        icon=icon,
        color=color,
        title=title,
        body=body,
        url=url,
        actor=actor,
        priority=priority,
        **source_kwargs,
    )
    invalidate_tags(_user_tag(recipient.id))
    notify_sse("notifications", recipient.id)
    if priority != "low":
        send_push_notification.delay(str(notif.uuid))
    return notif


def notify_many(
    *,
    recipients,
    origin,
    icon="",
    title,
    body="",
    url="",
    actor=None,
    priority="normal",
    color="",
    source=None,
):
    """Create notifications for multiple recipients and trigger SSE for each."""
    icon, color = _resolve_module_defaults(origin, icon, color)
    source_kwargs = {source_field(source): source} if source is not None else {}
    notifs = Notification.objects.bulk_create(
        [
            Notification(
                recipient=user,
                origin=origin,
                icon=icon,
                color=color,
                title=title,
                body=body,
                url=url,
                actor=actor,
                priority=priority,
                **source_kwargs,
            )
            for user in recipients
        ]
    )
    for user in recipients:
        invalidate_tags(_user_tag(user.id))
        notify_sse("notifications", user.id)
    if priority != "low":
        for notif in notifs:
            send_push_notification.delay(str(notif.uuid))
    return notifs


def notify_stream(
    *,
    recipient_ids,
    source,
    origin,
    title,
    body="",
    url="",
    actor=None,
    priority_map=None,
    default_priority="normal",
    icon="",
    color="",
    stream="",
):
    """Merge-or-create notifications keyed on a source object.

    For each recipient with an existing unread notification for *source*,
    the row is updated in place (title/body/actor, priority upgraded only,
    created_at bumped so it rises in the list) and no push is sent - unless
    the incoming priority is high/urgent (a mention must not be swallowed by
    the merge). Everyone else gets a fresh row plus a push. This is the
    generic form of chat's per-conversation merge.

    *stream* narrows the merge to rows carrying the same channel: the
    reminder crons pass ``"reminder"`` so their daily refresh can never
    repurpose a mention or an invitation that happens to share the source.
    """
    recipient_ids = list(recipient_ids)
    if not recipient_ids:
        return []
    field = source_field(source)
    icon, color = _resolve_module_defaults(origin, icon, color)
    priority_map = priority_map or {}

    existing = {
        n.recipient_id: n
        for n in Notification.objects.filter(
            recipient_id__in=recipient_ids,
            read_at__isnull=True,
            stream=stream,
            **{field: source},
        )
    }

    now = timezone.now()
    to_update, to_create, merged_to_push = [], [], []
    for uid in recipient_ids:
        priority = priority_map.get(uid, default_priority)
        notif = existing.get(uid)
        if notif:
            notif.title = title
            notif.body = body
            notif.url = url
            notif.actor = actor
            if _PRIORITY_RANK[priority] >= _PRIORITY_RANK["high"]:
                merged_to_push.append(notif)
            if _PRIORITY_RANK[priority] > _PRIORITY_RANK[notif.priority]:
                notif.priority = priority
            # auto_now_add only fires on INSERT, so setting created_at on the
            # update path is safe and intentional (bumps the row in the list).
            notif.created_at = now
            to_update.append(notif)
        else:
            to_create.append(
                Notification(
                    recipient_id=uid,
                    origin=origin,
                    icon=icon,
                    color=color,
                    title=title,
                    body=body,
                    url=url,
                    actor=actor,
                    priority=priority,
                    stream=stream,
                    **{field: source},
                )
            )

    if to_update:
        Notification.objects.bulk_update(
            to_update, ["title", "body", "url", "actor", "priority", "created_at"]
        )
    if to_create:
        # uuid_v7_or_v4 runs at __init__, so pks exist before bulk_create.
        Notification.objects.bulk_create(to_create)

    for uid in recipient_ids:
        invalidate_tags(_user_tag(uid))
        notify_sse("notifications", uid)
    # Dispatch after commit: inside an open transaction the worker could run
    # before the rows are visible and silently drop the push. One robust
    # callback per notification, so a broker error on one dispatch neither
    # swallows the others nor blocks unrelated on_commit callbacks.
    # A lambda rather than functools.partial: Django's robust-callback error
    # logging reads callback.__qualname__, which partial objects lack.
    for notif in [n for n in to_create if n.priority != "low"] + merged_to_push:
        transaction.on_commit(
            lambda uuid=str(notif.uuid): send_push_notification.delay(uuid),
            robust=True,
        )
    return to_update + to_create


def mark_source_read(user, source):
    """Mark the user's unread notifications for *source* as read.

    Called from the endpoints where the user demonstrably views the source
    (conversation mark-read, file comments, task comments, event and poll
    detail). Returns the number of rows marked.
    """
    field = source_field(source)
    marked = Notification.objects.filter(
        recipient=user,
        read_at__isnull=True,
        **{field: source},
    ).update(read_at=timezone.now())
    if marked:
        invalidate_tags(_user_tag(user.pk))
        notify_sse("notifications", user.pk)
    return marked


def mark_sources_read(user, sources):
    """Batch form of mark_source_read for a page of same-typed sources.

    Sources must share a model; the field is derived from the first one. Used
    where the user demonstrably sees many objects at once, such as a page of
    the mail message list. Returns the number of rows marked.
    """
    sources = list(sources)
    if not sources:
        return 0
    field = source_field(sources[0])
    marked = Notification.objects.filter(
        recipient=user,
        read_at__isnull=True,
        **{f"{field}__in": [s.pk for s in sources]},
    ).update(read_at=timezone.now())
    if marked:
        invalidate_tags(_user_tag(user.pk))
        notify_sse("notifications", user.pk)
    return marked


def settle_sources(sources, *, max_priority=None):
    """Mark every recipient's unread notifications for *sources* as read.

    ``mark_source_read``/``mark_sources_read`` record that one user saw a
    source; this records that the sources resolved themselves (task completed,
    due date pushed back), so their notifications are settled for everyone.

    ``max_priority`` bounds what gets settled: pass ``"normal"`` to leave
    high/urgent rows untouched - completing a task proves its due reminder is
    moot, not that its mentions were seen. Sources must share a model.
    Returns the number of rows marked.
    """
    sources = list(sources)
    if not sources:
        return 0
    field = source_field(sources[0])
    qs = Notification.objects.filter(
        read_at__isnull=True,
        **{f"{field}__in": [s.pk for s in sources]},
    )
    if max_priority is not None:
        ceiling = _PRIORITY_RANK[max_priority]
        qs = qs.filter(
            priority__in=[p for p, rank in _PRIORITY_RANK.items() if rank <= ceiling]
        )
    recipient_ids = list(qs.values_list("recipient_id", flat=True).distinct())
    if not recipient_ids:
        return 0
    marked = qs.update(read_at=timezone.now())
    for uid in recipient_ids:
        invalidate_tags(_user_tag(uid))
        notify_sse("notifications", uid)
    return marked


@cached(
    key=lambda user: f"notif:unread:{user.pk}",
    ttl=_UNREAD_TTL,
    tags=lambda user: [_user_tag(user.pk)],
)
def get_unread_count(user):
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()


@cached(
    key=lambda user: f"notif:badges:{user.pk}",
    ttl=_UNREAD_TTL,
    tags=lambda user: [_user_tag(user.pk)],
)
def get_unread_badges(user):
    """Per-module unread counts for the dashboard tiles.

    Returns ``{origin: {"count": n, "url": deep_link}}``. ``url`` is set only
    when the module has exactly one unread notification carrying a url, so
    the tile can open that item directly; with several the target is
    ambiguous and the tile falls back to the module home.
    """
    unread = Notification.objects.filter(recipient=user, read_at__isnull=True)
    badges = {
        row["origin"]: {"count": row["n"], "url": None}
        for row in unread.values("origin").annotate(n=Count("uuid"))
    }
    singles = [origin for origin, b in badges.items() if b["count"] == 1]
    for origin, url in unread.filter(origin__in=singles).values_list("origin", "url"):
        badges[origin]["url"] = url or None
    return badges
