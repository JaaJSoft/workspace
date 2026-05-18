"""Action runner for the rules engine.

Each handler returns a dict ``{type, ok, ...details}`` that the engine
includes in the ``MailRuleLog.actions_applied`` audit field. Errors are
caught and turned into ``{ok: False, error: '<kind>'}`` so a single
failure doesn't abort the rest of the rule.
"""
import logging
from typing import Any

from django.db import transaction

from workspace.common.logging import scrub
from ...models import (
    MailFolder, MailLabel, MailMessage, MailMessageLabel,
)
from .schema import (
    AddLabelAction,
    DeleteAction,
    MarkReadAction,
    MarkUnreadAction,
    MoveToFolderAction,
    RemoveLabelAction,
    StarAction,
    UnstarAction,
)

logger = logging.getLogger(__name__)


def _ok(type_: str, **details) -> dict:
    return {'type': type_, 'ok': True, **details}


def _err(type_: str, error: str) -> dict:
    return {'type': type_, 'ok': False, 'error': error}


def _add_label(action: AddLabelAction, message: MailMessage) -> dict:
    try:
        label = MailLabel.objects.get(uuid=action.label_id, account_id=message.account_id)
    except MailLabel.DoesNotExist:
        return _err('add_label', 'label_not_found')
    MailMessageLabel.objects.get_or_create(message=message, label=label)
    return _ok('add_label', label_id=str(label.uuid))


def _remove_label(action: RemoveLabelAction, message: MailMessage) -> dict:
    deleted, _ = MailMessageLabel.objects.filter(
        message=message, label_id=action.label_id,
    ).delete()
    return _ok('remove_label', label_id=str(action.label_id), removed=deleted)


HANDLERS: dict[type, Any] = {
    AddLabelAction: _add_label,
    RemoveLabelAction: _remove_label,
}


def apply_action(action, message: MailMessage) -> dict:
    """Dispatch ``action`` against ``message`` and return an audit dict.

    Wraps the handler in a try/except so a failing action records the error
    rather than aborting the rule. Callers (the engine) decide whether to
    short-circuit based on the action type, not on success.
    """
    handler = HANDLERS.get(type(action))
    if handler is None:
        return _err(getattr(action, 'type', 'unknown'), 'no_handler')
    try:
        with transaction.atomic():
            return handler(action, message)
    except Exception as e:
        logger.exception('action %s failed on message %s', action.type, message.uuid)
        return _err(action.type, scrub(str(e))[:200])
