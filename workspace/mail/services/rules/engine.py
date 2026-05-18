"""Rules engine entry point.

``run_rules_for_messages(account, message_uuids)`` loads all enabled rules
for the account in position order, evaluates each against each message,
applies matching rules' actions, writes audit logs, and updates rule stats.

Designed to be called from the sync pipeline once new messages have been
persisted. Catches and logs all exceptions so a misbehaving rule never
breaks the sync.
"""
import logging
from typing import Iterable

from django.db.models import F
from django.utils import timezone

from ...models import MailMessage, MailRule, MailRuleLog
from .actions import apply_action
from .conditions import evaluate_node
from .schema import (
    DeleteAction,
    SchemaError,
    parse_actions,
    parse_conditions,
)

logger = logging.getLogger(__name__)


def _load_rules(account):
    return list(
        MailRule.objects
        .filter(account=account, is_enabled=True)
        .order_by('position', 'created_at')
    )


def _matches(rule, message) -> bool:
    try:
        node = parse_conditions(rule.conditions)
    except SchemaError:
        logger.warning('rule %s has invalid conditions, skipping', rule.uuid)
        return False
    try:
        return evaluate_node(node, message)
    except Exception:
        logger.exception('eval failed for rule %s on message %s', rule.uuid, message.uuid)
        return False


def _apply(rule, message) -> tuple[list, bool]:
    """Apply ``rule.actions`` to ``message``. Returns (audit_list, short_circuit).

    ``short_circuit=True`` when an action removed the message from further
    consideration (currently: ``delete``). The engine uses this to stop
    evaluating further rules for the same message.
    """
    try:
        actions = parse_actions(rule.actions)
    except SchemaError:
        logger.warning('rule %s has invalid actions, skipping', rule.uuid)
        return [], False

    audit = []
    short_circuit = False
    for action in actions:
        result = apply_action(action, message)
        audit.append(result)
        if isinstance(action, DeleteAction) and result.get('ok'):
            short_circuit = True
            break
    return audit, short_circuit


def run_rules_for_messages(account, message_uuids: Iterable[str]) -> dict:
    """Evaluate all enabled rules for ``account`` against each message.

    Returns ``{message_uuid: [rule_uuid, ...]}`` listing which rules
    matched per message (whether their actions succeeded or not).
    """
    uuids = list(message_uuids)
    if not uuids:
        return {}
    rules = _load_rules(account)
    if not rules:
        return {}

    messages = list(
        MailMessage.objects
        .filter(account=account, uuid__in=uuids, deleted_at__isnull=True)
        .select_related('account', 'folder')
    )

    summary: dict = {}
    now = timezone.now()
    for message in messages:
        matched_rules: list[MailRule] = []
        for rule in rules:
            if not _matches(rule, message):
                continue
            audit, short_circuit = _apply(rule, message)
            matched_rules.append(rule)
            try:
                MailRuleLog.objects.create(
                    rule=rule, rule_name_snapshot=rule.name,
                    message=message, actions_applied=audit,
                )
            except Exception:
                logger.exception('failed to write rule log for %s / %s', rule.uuid, message.uuid)
            if short_circuit:
                break
            if rule.stop_processing:
                break
        if matched_rules:
            summary[str(message.uuid)] = [str(r.uuid) for r in matched_rules]
            MailRule.objects.filter(pk__in=[r.pk for r in matched_rules]).update(
                match_count=F('match_count') + 1,
                last_matched_at=now,
            )
    return summary
