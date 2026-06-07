"""Rules engine entry point.

``run_rules_for_messages(account, message_uuids)`` loads all enabled rules
for the account in position order, evaluates each against each message,
applies matching rules' actions, writes audit logs, and updates rule stats.

Designed to be called from the sync pipeline once new messages have been
persisted. Catches and logs all exceptions so a misbehaving rule never
breaks the sync.
"""

import logging
from collections.abc import Iterable

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
        MailRule.objects.filter(account=account, is_enabled=True).order_by(
            "position", "created_at"
        )
    )


def _matches(rule, message) -> bool:
    try:
        node = parse_conditions(rule.conditions)
    except SchemaError:
        logger.warning("rule %s has invalid conditions, skipping", rule.uuid)
        return False
    try:
        return evaluate_node(node, message)
    except Exception:
        logger.exception(
            "eval failed for rule %s on message %s", rule.uuid, message.uuid
        )
        return False


def _apply(rule, message) -> tuple[list, bool, bool]:
    """Apply ``rule.actions`` to ``message``.

    Returns ``(audit_list, short_circuit, applied)``:

    - ``short_circuit=True`` when an action removed the message from further
      consideration (currently: ``delete``). The engine uses this to stop
      evaluating further rules for the same message.
    - ``applied=False`` when ``rule.actions`` could not be parsed. The
      engine uses this to skip stat increments and log writes for rules
      whose actions are broken.
    """
    try:
        actions = parse_actions(rule.actions)
    except SchemaError:
        logger.warning("rule %s has invalid actions, skipping", rule.uuid)
        return [], False, False

    audit = []
    short_circuit = False
    for action in actions:
        result = apply_action(action, message)
        audit.append(result)
        if isinstance(action, DeleteAction) and result.get("ok"):
            short_circuit = True
            break
    return audit, short_circuit, True


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
        MailMessage.objects.filter(
            account=account, uuid__in=uuids, deleted_at__isnull=True
        ).select_related("account", "folder")
    )

    summary: dict = {}
    now = timezone.now()
    for message in messages:
        matched_rules: list[MailRule] = []
        for rule in rules:
            if not _matches(rule, message):
                continue
            audit, short_circuit, applied = _apply(rule, message)
            if not applied:
                # Conditions matched but actions could not be parsed -
                # skip stats and log so the rule doesn't appear to have
                # fired when it actually did nothing.
                continue
            matched_rules.append(rule)
            try:
                MailRuleLog.objects.create(
                    rule=rule,
                    rule_name_snapshot=rule.name,
                    message=message,
                    actions_applied=audit,
                )
            except Exception:
                logger.exception(
                    "failed to write rule log for %s / %s", rule.uuid, message.uuid
                )
            if short_circuit:
                break
            if rule.stop_processing:
                break
        if matched_rules:
            summary[str(message.uuid)] = [str(r.uuid) for r in matched_rules]
            MailRule.objects.filter(pk__in=[r.pk for r in matched_rules]).update(
                match_count=F("match_count") + 1,
                last_matched_at=now,
            )
    return summary


def _count_imap_failures(audit: list) -> int:
    """Number of action results that flagged an IMAP failure.

    Move/delete/flag handlers record either ``error == 'imap_failed'`` (move)
    or ``imap_warning == 'imap_failed'`` (flag/delete) in their audit dict.
    """
    failures = 0
    for entry in audit:
        if (
            entry.get("error") == "imap_failed"
            or entry.get("imap_warning") == "imap_failed"
        ):
            failures += 1
    return failures


def apply_rule_to_folder(rule, folder, *, dry_run: bool, limit: int = 500) -> dict:
    """Evaluate ``rule`` against the messages of ``folder`` and optionally apply.

    Unlike the sync engine this targets *existing* messages and runs a single
    rule regardless of ``rule.is_enabled`` (explicit user action). Scans the
    ``limit`` most recent non-deleted messages; ``capped`` is True when the
    folder holds more than ``limit``.

    Returns ``{scanned, total, matched, applied, imap_failed, capped}``.
    """
    base = MailMessage.objects.filter(
        account=rule.account,
        folder=folder,
        deleted_at__isnull=True,
    )
    total = base.count()
    # nulls_last keeps the "most recent" cap deterministic across backends
    # (Postgres sorts NULLs first on DESC, SQLite sorts them last by default).
    messages = list(
        base.select_related("account", "folder").order_by(
            F("date").desc(nulls_last=True)
        )[:limit]
    )

    matched = 0
    applied = 0
    imap_failed = 0
    matched_pks: list = []
    for message in messages:
        if not _matches(rule, message):
            continue
        matched += 1
        if dry_run:
            continue
        audit, _short_circuit, ok = _apply(rule, message)
        if not ok:
            continue
        applied += 1
        imap_failed += _count_imap_failures(audit)
        matched_pks.append(message.pk)
        try:
            MailRuleLog.objects.create(
                rule=rule,
                rule_name_snapshot=rule.name,
                message=message,
                actions_applied=audit,
            )
        except Exception:
            logger.exception(
                "failed to write rule log for %s / %s", rule.uuid, message.uuid
            )

    if matched_pks:
        MailRule.objects.filter(pk=rule.pk).update(
            match_count=F("match_count") + len(matched_pks),
            last_matched_at=timezone.now(),
        )

    return {
        "scanned": len(messages),
        "total": total,
        "matched": matched,
        "applied": applied,
        "imap_failed": imap_failed,
        "capped": total > limit,
    }
