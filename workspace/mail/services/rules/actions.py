"""Action runner for the rules engine.

Each handler returns a dict ``{type, ok, ...details}`` that the engine
includes in the ``MailRuleLog.actions_applied`` audit field. Errors are
caught and turned into ``{ok: False, error: '<kind>'}`` so a single
failure doesn't abort the rest of the rule.
"""

import logging
from typing import Any

from workspace.common.logging import scrub

from ...models import MailFolder, MailLabel, MailMessage
from ..notifications import settle_message_notifications
from ..triage import move_to_folder, set_flag, set_label, trash_message
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
    return {"type": type_, "ok": True, **details}


def _err(type_: str, error: str) -> dict:
    return {"type": type_, "ok": False, "error": error}


def _add_label(action: AddLabelAction, message: MailMessage) -> dict:
    try:
        label = MailLabel.objects.get(
            uuid=action.label_id, account_id=message.account_id
        )
    except MailLabel.DoesNotExist:
        return _err("add_label", "label_not_found")
    set_label(message, label, attached=True)
    return _ok("add_label", label_id=str(label.uuid))


def _remove_label(action: RemoveLabelAction, message: MailMessage) -> dict:
    label = MailLabel.objects.filter(
        uuid=action.label_id, account_id=message.account_id
    ).first()
    removed = label is not None and set_label(message, label, attached=False)
    return _ok("remove_label", label_id=str(action.label_id), removed=int(removed))


def _flag(message: MailMessage, flag: str, type_: str) -> dict:
    result = _ok(type_)
    if not set_flag(message, flag):
        logger.warning("IMAP %s failed for message %s", type_, message.uuid)
        result["imap_warning"] = "imap_failed"
    return result


def _mark_read(action: MarkReadAction, message: MailMessage) -> dict:
    return _flag(message, "read", "mark_read")


def _mark_unread(action: MarkUnreadAction, message: MailMessage) -> dict:
    return _flag(message, "unread", "mark_unread")


def _star(action: StarAction, message: MailMessage) -> dict:
    return _flag(message, "starred", "star")


def _unstar(action: UnstarAction, message: MailMessage) -> dict:
    return _flag(message, "unstarred", "unstar")


def _move_to_folder(action: MoveToFolderAction, message: MailMessage) -> dict:
    try:
        target = MailFolder.objects.get(
            uuid=action.folder_id,
            account_id=message.account_id,
        )
    except MailFolder.DoesNotExist:
        return _err("move_to_folder", "folder_not_in_account")

    from ...queries import canonical_folder, canonical_folder_id

    target = canonical_folder(target)
    if canonical_folder_id(message.folder) == target.pk:
        return _ok("move_to_folder", folder_id=str(target.uuid), noop=True)
    try:
        move_to_folder(message, target)
    except Exception as e:
        logger.warning("IMAP move failed for message %s: %s", message.uuid, scrub(e))
        return _err("move_to_folder", "imap_failed")
    return _ok("move_to_folder", folder_id=str(target.uuid))


def _delete(action: DeleteAction, message: MailMessage) -> dict:
    """Move the message to Trash. A rule never deletes anything for good.

    It runs unattended, on every incoming message, from conditions that can
    match more than their author meant: a message already in Trash stays
    there, and an account without one keeps the message where it is.
    """
    from ...queries import special_folder

    try:
        outcome = trash_message(message, expunge=False)
    except Exception as e:
        logger.warning("IMAP move failed for message %s: %s", message.uuid, scrub(e))
        return _err("delete", "imap_failed")
    if outcome is None:
        if special_folder(message.account, MailFolder.FolderType.TRASH) is None:
            return _err("delete", "no_trash_folder")
        # Not redundant: apply_rule_to_folder runs this handler against
        # existing messages, which can carry an unread notification.
        settle_message_notifications(message.account.owner, [message.pk])
        return _ok("delete", noop=True)
    return _ok("delete")


HANDLERS: dict[type, Any] = {
    AddLabelAction: _add_label,
    RemoveLabelAction: _remove_label,
    MarkReadAction: _mark_read,
    MarkUnreadAction: _mark_unread,
    StarAction: _star,
    UnstarAction: _unstar,
    MoveToFolderAction: _move_to_folder,
    DeleteAction: _delete,
}


def apply_action(action, message: MailMessage) -> dict:
    """Dispatch ``action`` against ``message`` and return an audit dict.

    Wraps the handler in a try/except so a failing action records the error
    rather than aborting the rule. Callers (the engine) decide whether to
    short-circuit based on the action type, not on success.
    """
    handler = HANDLERS.get(type(action))
    if handler is None:
        return _err(getattr(action, "type", "unknown"), "no_handler")
    try:
        # No outer transaction.atomic here: IMAP calls would otherwise
        # extend row locks across a network round-trip. Multi-statement DB
        # work (a write and its counter refresh) has its own tight atomic
        # block in the triage service the handler calls.
        return handler(action, message)
    except Exception as e:
        logger.exception("action %s failed on message %s", action.type, message.uuid)
        return _err(action.type, scrub(str(e))[:200])
