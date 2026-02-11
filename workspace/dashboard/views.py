from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, OuterRef, Prefetch, Q, Subquery, Sum
from django.shortcuts import render
from django.utils import timezone

from workspace.calendar.models import Event, EventMember
from workspace.chat.models import Conversation, ConversationMember, Message
from workspace.chat.services import get_unread_counts
from workspace.core.module_registry import registry
from workspace.files.models import File, FileFavorite

INSIGHTS_LIMIT = 6


def _get_stats(user):
    base_qs = File.objects.filter(owner=user, deleted_at__isnull=True)
    aggregates = base_qs.aggregate(
        file_count=Count('pk', filter=Q(node_type=File.NodeType.FILE)),
        total_size=Sum('size', filter=Q(node_type=File.NodeType.FILE)),
    )

    unread = get_unread_counts(user)

    now = timezone.now()
    upcoming_events = Event.objects.filter(
        Q(owner=user) | Q(members__user=user, members__status__in=[
            EventMember.Status.ACCEPTED, EventMember.Status.PENDING,
        ]),
        start__gte=now,
        start__lte=now + timedelta(days=7),
    ).distinct().count()

    return {
        'file_count': aggregates['file_count'] or 0,
        'total_size': aggregates['total_size'] or 0,
        'unread_messages': unread.get('total', 0),
        'upcoming_events': upcoming_events,
    }


def _get_recent_nodes(user, limit=INSIGHTS_LIMIT):
    return File.objects.filter(
        owner=user,
        deleted_at__isnull=True,
    ).select_related('parent').order_by('-updated_at')[:limit]


def _get_favorite_nodes(user, limit=INSIGHTS_LIMIT):
    favorites = FileFavorite.objects.filter(
        owner=user,
        file__deleted_at__isnull=True,
    ).select_related('file', 'file__parent').order_by('-created_at')[:limit]
    return [favorite.file for favorite in favorites]


def _get_trash_nodes(user, limit=INSIGHTS_LIMIT):
    return File.objects.filter(
        owner=user,
        deleted_at__isnull=False,
    ).select_related('parent').order_by('-deleted_at')[:limit]


def _get_recent_conversations(user, limit=INSIGHTS_LIMIT):
    member_convos = ConversationMember.objects.filter(
        user=user, left_at__isnull=True,
    ).values_list('conversation_id', flat=True)

    conversations = (
        Conversation.objects.filter(uuid__in=member_convos)
        .prefetch_related(
            Prefetch(
                'members',
                queryset=ConversationMember.objects.filter(
                    left_at__isnull=True,
                ).select_related('user'),
            ),
        )
        .order_by('-updated_at')[:limit]
    )

    last_msg_subquery = (
        Message.objects.filter(
            conversation=OuterRef('pk'), deleted_at__isnull=True,
        ).order_by('-created_at').values('uuid')[:1]
    )
    conversations = conversations.annotate(_last_msg_id=Subquery(last_msg_subquery))
    conv_list = list(conversations)

    last_msg_ids = [c._last_msg_id for c in conv_list if c._last_msg_id]
    last_msgs = {
        m.uuid: m
        for m in Message.objects.filter(uuid__in=last_msg_ids).select_related('author')
    }

    unread_map = get_unread_counts(user).get('conversations', {})

    now = timezone.now()
    for c in conv_list:
        c._last_message = last_msgs.get(c._last_msg_id)
        c.unread_count = unread_map.get(str(c.uuid), 0)

        active_members = list(c.members.all())
        other_members = [m for m in active_members if m.user_id != user.id]

        if c.title:
            c.display_name = c.title
        elif c.kind == Conversation.Kind.DM and other_members:
            c.display_name = other_members[0].user.username
        else:
            names = [m.user.username for m in other_members[:3]]
            c.display_name = ', '.join(names) if names else 'Group'

        if c.kind == Conversation.Kind.DM and other_members:
            c.avatar_initial = other_members[0].user.username[0].upper()
        else:
            initials = [m.user.username[0].upper() for m in other_members[:2]]
            c.avatar_initial = ''.join(initials) or 'G'

        if c._last_message:
            body = c._last_message.body
            if body:
                if len(body) > 30:
                    body = body[:30] + '\u2026'
                c.last_message_preview = f'{c._last_message.author.username}: {body}'
            else:
                c.last_message_preview = f'{c._last_message.author.username}: '
            diff = (now - c._last_message.created_at).total_seconds()
            if diff < 60:
                c.time_ago = 'now'
            elif diff < 3600:
                c.time_ago = f'{int(diff // 60)}m'
            elif diff < 86400:
                c.time_ago = f'{int(diff // 3600)}h'
            elif diff < 604800:
                c.time_ago = f'{int(diff // 86400)}d'
            else:
                c.time_ago = c._last_message.created_at.strftime('%b %d')
        else:
            c.last_message_preview = 'No messages yet'
            c.time_ago = ''

    return conv_list


def _get_upcoming_events(user, limit=INSIGHTS_LIMIT):
    now = timezone.now()
    return Event.objects.filter(
        Q(owner=user) | Q(members__user=user, members__status__in=[
            EventMember.Status.ACCEPTED, EventMember.Status.PENDING,
        ]),
        start__gte=now,
        start__lte=now + timedelta(days=7),
    ).select_related('calendar', 'owner').distinct().order_by('start')[:limit]


def _build_dashboard_context(
    user,
    include_stats=True,
    include_recent=True,
    include_favorites=True,
    include_trash=True,
    include_conversations=False,
    include_events=False,
):
    context = {
        'modules': [m for m in registry.get_for_template() if m['slug'] != 'dashboard'],
    }
    if include_stats:
        context['stats'] = _get_stats(user)
    if include_recent:
        context['recent_nodes'] = _get_recent_nodes(user)
    if include_favorites:
        context['favorite_nodes'] = _get_favorite_nodes(user)
    if include_trash:
        context['trash_nodes'] = _get_trash_nodes(user)
    if include_conversations:
        context['recent_conversations'] = _get_recent_conversations(user)
    if include_events:
        context['upcoming_events'] = _get_upcoming_events(user)
    return context


def _render_insights(request, tab, template_name):
    if request.headers.get('X-Alpine-Request'):
        context = _build_dashboard_context(
            request.user,
            include_stats=False,
            include_recent=tab == 'recent',
            include_favorites=tab == 'favorites',
            include_trash=tab == 'trash',
            include_conversations=tab == 'conversations',
            include_events=tab == 'events',
        )
        return render(request, template_name, context)

    context = _build_dashboard_context(
        request.user,
        include_conversations=tab == 'conversations',
        include_events=tab == 'events',
    )
    context['insight_tab'] = tab
    return render(request, 'dashboard/index.html', context)


@login_required
def index(request):
    """Dashboard home page."""
    context = _build_dashboard_context(request.user)
    context['insight_tab'] = 'recent'
    return render(request, 'dashboard/index.html', context)


@login_required
def stats(request):
    if request.headers.get('X-Alpine-Request'):
        context = _build_dashboard_context(
            request.user,
            include_recent=False,
            include_favorites=False,
            include_trash=False,
        )
        return render(request, 'dashboard/partials/stats.html', context)

    context = _build_dashboard_context(request.user)
    context['insight_tab'] = 'recent'
    return render(request, 'dashboard/index.html', context)


@login_required
def insights_recent(request):
    return _render_insights(
        request,
        tab='recent',
        template_name='dashboard/partials/insights_recent.html',
    )


@login_required
def insights_conversations(request):
    return _render_insights(
        request,
        tab='conversations',
        template_name='dashboard/partials/insights_conversations.html',
    )


@login_required
def insights_events(request):
    return _render_insights(
        request,
        tab='events',
        template_name='dashboard/partials/insights_events.html',
    )


@login_required
def insights_favorites(request):
    return _render_insights(
        request,
        tab='favorites',
        template_name='dashboard/partials/insights_favorites.html',
    )


@login_required
def insights_trash(request):
    return _render_insights(
        request,
        tab='trash',
        template_name='dashboard/partials/insights_trash.html',
    )
