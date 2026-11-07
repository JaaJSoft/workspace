"""Editing a message that may be deleted while the edit is under way."""

from django.utils import timezone

from ..models import Message
from .mentions import build_mention_map
from .rendering import render_message_body


def edit_message(message, body):
    """Replace the text of *message* with *body*; False if it was deleted.

    Conditional on the message still being live: a delete that landed after
    the message was loaded has purged its text, and writing the edit over it
    would bring the content of a deleted message back.
    """
    mention_map, _ = build_mention_map(body)
    body_html = render_message_body(body, mention_map=mention_map or None)
    edited_at = timezone.now()
    edited = Message.objects.filter(pk=message.pk, deleted_at__isnull=True).update(
        body=body, body_html=body_html, edited_at=edited_at
    )
    if not edited:
        return False
    message.body = body
    message.body_html = body_html
    message.edited_at = edited_at
    return True
