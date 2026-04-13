import re

import mistune
from django.db import transaction
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

from workspace.core.sse_registry import notify_sse


def user_conversation_ids(user):
    """Return conversation UUIDs where the user is an active member."""
    from .models import ConversationMember

    return ConversationMember.objects.filter(
        user=user, left_at__isnull=True,
    ).values_list('conversation_id', flat=True)


def get_active_membership(user, conversation_id):
    """Return the active ConversationMember for *user* in *conversation_id*, or None."""
    from .models import ConversationMember

    return ConversationMember.objects.filter(
        conversation_id=conversation_id,
        user=user,
        left_at__isnull=True,
    ).first()


@transaction.atomic
def get_or_create_dm(user, other_user):
    """Get or create a DM conversation between two users.

    Deduplicates by finding an existing DM with exactly these two active members.
    If a member had left, reactivates them.
    """
    from .models import Conversation, ConversationMember

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

    # Create new DM
    conversation = Conversation.objects.create(
        kind=Conversation.Kind.DM,
        created_by=user,
    )
    ConversationMember.objects.bulk_create([
        ConversationMember(conversation=conversation, user=user),
        ConversationMember(conversation=conversation, user=other_user),
    ])
    return conversation


def get_unread_counts(user):
    """Return unread message counts for each conversation the user is in."""
    from .models import ConversationMember

    memberships = ConversationMember.objects.filter(
        user=user,
        left_at__isnull=True,
        unread_count__gt=0,
    ).values_list('conversation_id', 'unread_count')

    conversations = {}
    total = 0
    for conv_id, count in memberships:
        conversations[str(conv_id)] = count
        total += count

    return {'total': total, 'conversations': conversations}


class _ChatRenderer(mistune.HTMLRenderer):
    """Markdown renderer with Pygments syntax highlighting for code blocks."""

    _formatter = HtmlFormatter(nowrap=True)

    def block_code(self, code, info=None):
        lang = None
        if info:
            lang = info.strip().split()[0]
        try:
            if lang:
                lexer = get_lexer_by_name(lang)
            else:
                lexer = guess_lexer(code)
        except ClassNotFound:
            lexer = TextLexer()

        highlighted = highlight(code, lexer, self._formatter)
        lang_attr = f' data-lang="{mistune.escape(lang)}"' if lang else ''
        return f'<pre class="code-block"{lang_attr}><code>{highlighted}</code></pre>\n'

    def codespan(self, text):
        return f'<code class="code-inline">{mistune.escape(text)}</code>'

    def image(self, alt, url, title=None):
        # Strip AI-generated <img> tags — real images come through attachments.
        return f'({mistune.escape(alt)})' if alt else ''


# Markdown renderer configured for chat with syntax highlighting
_markdown = mistune.create_markdown(
    renderer=_ChatRenderer(escape=True),
    plugins=['strikethrough', 'url', 'table', 'task_lists'],
)


_MENTION_PREFIX = 'MNTN__'
_MENTION_SUFFIX = '__MNTN'


def render_message_body(body, mention_map=None):
    """Render markdown body to HTML suitable for chat messages.

    If mention_map is provided (dict of username -> user_id), @username tokens
    matching those usernames are rendered as mention badges with hover cards.
    Mentions are replaced with placeholders in raw text before markdown rendering
    to avoid corrupting URLs or code blocks.
    """
    if mention_map:
        placeholders = {}

        def _placeholder(match):
            username = match.group(1)
            if username in mention_map or username == 'everyone':
                key = f'{_MENTION_PREFIX}{username}{_MENTION_SUFFIX}'
                user_id = mention_map.get(username)
                placeholders[key] = _mention_badge(username, user_id)
                return key
            return match.group(0)

        body = re.sub(r'(?:(?<=\s)|(?<=^))@(\w+)', _placeholder, body, flags=re.MULTILINE)
        html = _markdown(body)
        for key, badge in placeholders.items():
            html = html.replace(key, badge)
        return html

    return _markdown(body)


def _mention_badge(username, user_id=None):
    if username == 'everyone':
        return '<span class="mention-badge mention-everyone">@everyone</span>'
    if user_id:
        return (
            f'<span class="mention-badge" data-username="{username}" data-user-id="{user_id}"'
            f' onmouseenter="window._userCardShow(this,{user_id})"'
            f' onmouseleave="window._userCardScheduleHide(this)"'
            f'>@{username}</span>'
        )
    return f'<span class="mention-badge" data-username="{username}">@{username}</span>'


def extract_mentions(body):
    """Extract @username tokens from message body text.

    Returns a set of usernames (excluding 'everyone') and whether @everyone was used.
    """
    tokens = set(re.findall(r'@(\w+)', body))
    has_everyone = 'everyone' in tokens
    tokens.discard('everyone')
    return tokens, has_everyone


def notify_conversation_members(conversation, exclude_user=None):
    """Update SSE cache keys for all active members of a conversation."""
    from .models import ConversationMember

    member_user_ids = ConversationMember.objects.filter(
        conversation=conversation,
        left_at__isnull=True,
    ).values_list('user_id', flat=True)

    for uid in member_user_ids:
        if exclude_user and uid == exclude_user.id:
            continue
        notify_sse('chat', uid)


def notify_new_message(conversation, author, body, mentioned_user_ids=None, mention_everyone=False):
    """Send push notifications for a new chat message.

    Merges into existing unread notifications for the same conversation:
    - First message: creates a new notification + sends push
    - Subsequent messages within 60s: updates the existing notification body/title
      (no duplicate push)
    """
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from workspace.notifications.models import Notification
    from workspace.notifications.services import _resolve_module_defaults
    from workspace.notifications.tasks import send_push_notification
    from workspace.core.sse_registry import notify_sse as _notify_sse
    from .models import ConversationMember

    mentioned_user_ids = mentioned_user_ids or set()

    User = get_user_model()
    member_ids = list(
        ConversationMember.objects.filter(
            conversation=conversation,
            left_at__isnull=True,
        ).exclude(user=author).values_list('user_id', flat=True)
    )
    if not member_ids:
        return

    author_name = author.get_full_name() or author.username
    conv_title = conversation.title
    conv_url = f'/chat/{conversation.pk}'
    preview = (body[:150] + '...') if len(body) > 150 else body

    if conv_title:
        title_single = f'{author_name} in {conv_title}'
    else:
        title_single = author_name

    icon, color = _resolve_module_defaults('chat', '', '')

    for uid in member_ids:
        is_mentioned = uid in mentioned_user_ids or mention_everyone
        priority = 'high' if is_mentioned else 'normal'

        # Try to merge into an existing unread notification for this conversation
        existing = Notification.objects.filter(
            recipient_id=uid,
            origin='chat',
            url=conv_url,
            read_at__isnull=True,
        ).first()

        if existing:
            # Merge: update body/title, bump timestamp
            existing.body = preview
            existing.title = title_single
            existing.actor = author
            if is_mentioned and existing.priority != 'urgent':
                existing.priority = priority
            existing.save(update_fields=['body', 'title', 'actor', 'priority'])
            # Bump created_at so it rises to the top of the list
            Notification.objects.filter(pk=existing.pk).update(created_at=timezone.now())
            # Refresh the bell icon count via SSE (count unchanged but content updated)
            _notify_sse('notifications', uid)
        else:
            # First message: create notification + send push
            notif = Notification.objects.create(
                recipient_id=uid,
                origin='chat',
                icon=icon,
                color=color,
                title=title_single,
                body=preview,
                url=conv_url,
                actor=author,
                priority=priority,
            )
            _notify_sse('notifications', uid)
            send_push_notification.delay(str(notif.uuid))


def notify_user(user_id):
    """Mark that a user has pending SSE events."""
    notify_sse('chat', user_id)
