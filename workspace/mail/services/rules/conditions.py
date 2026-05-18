"""Condition tree evaluator.

Pure functions: take a parsed condition node (from ``schema.py``) and a
``MailMessage`` row, return ``bool``. The evaluator never writes to the DB
and never touches IMAP - it is safe to run inside any transaction.
"""
import logging
from datetime import datetime
from typing import Any, Iterable, Union

from .schema import (
    BOOL_FIELDS,
    DATE_FIELDS,
    GroupCondition,
    LeafCondition,
    TEXT_FIELDS,
)

logger = logging.getLogger(__name__)

# Bounded timeout so a pathological regex cannot stall the IMAP sync.
REGEX_TIMEOUT_SECONDS = 1.0


def _addr_strings(addr_list: Iterable[Any]) -> list:
    """Flatten a list of ``{name, email}`` dicts to the strings the operator
    should search through (both name and email considered)."""
    out = []
    for entry in addr_list or []:
        if isinstance(entry, dict):
            if entry.get('email'):
                out.append(entry['email'])
            if entry.get('name'):
                out.append(entry['name'])
        elif entry:
            out.append(str(entry))
    return out


def _text_field_values(message, field: str) -> list:
    """Return the list of strings to search for a given text field."""
    if field == 'from':
        return _addr_strings([message.from_address])
    if field == 'to':
        return _addr_strings(message.to_addresses)
    if field == 'cc':
        return _addr_strings(message.cc_addresses)
    if field == 'recipient':
        return _addr_strings(message.to_addresses) + _addr_strings(message.cc_addresses)
    if field == 'subject':
        return [message.subject or '']
    if field == 'body':
        text = message.body_text or ''
        if not text and message.body_html:
            # Best-effort plain extraction so users can match on body even
            # when the message only has an HTML part.
            import re
            text = re.sub(r'<[^>]+>', ' ', message.body_html)
        return [text]
    if field == 'folder':
        return [message.folder.name if message.folder_id else '']
    return []


def _bool_field_value(message, field: str) -> bool:
    if field == 'has_attachments':
        return bool(message.has_attachments)
    if field == 'is_starred':
        return bool(message.is_starred)
    return False


def _date_field_value(message, field: str) -> Union[datetime, None]:
    if field == 'date':
        return message.date
    return None


def _normalize(s: str, case_sensitive: bool) -> str:
    return s if case_sensitive else (s or '').lower()


def _eval_text(values: list, op: str, value: Any, case_sensitive: bool) -> bool:
    if op == 'in_list':
        needles = [_normalize(v, case_sensitive) for v in value]
        haystacks = [_normalize(v, case_sensitive) for v in values]
        return any(n == h for n in needles for h in haystacks)

    needle = _normalize(value, case_sensitive) if isinstance(value, str) else value
    for raw in values:
        hay = _normalize(raw, case_sensitive)
        if op == 'contains' and needle in hay:
            return True
        if op == 'equals' and needle == hay:
            return True
        if op == 'starts_with' and hay.startswith(needle):
            return True
        if op == 'ends_with' and hay.endswith(needle):
            return True
        if op == 'matches_regex':
            try:
                import regex
                if regex.search(needle, hay, timeout=REGEX_TIMEOUT_SECONDS):
                    return True
            except Exception:
                logger.warning('regex eval failed (timeout or bad pattern)')
                return False
    return False


def _eval_leaf(node: LeafCondition, message) -> bool:
    if node.field in TEXT_FIELDS:
        return _eval_text(
            _text_field_values(message, node.field),
            node.op, node.value, node.case_sensitive,
        )
    if node.field in BOOL_FIELDS:
        actual = _bool_field_value(message, node.field)
        if node.op == 'is_true':
            return actual is True
        if node.op == 'is_false':
            return actual is False
        return False
    if node.field in DATE_FIELDS:
        actual = _date_field_value(message, node.field)
        if actual is None or not isinstance(node.value, str):
            return False
        try:
            target = datetime.fromisoformat(node.value)
        except ValueError:
            return False
        if node.op == 'greater_than':
            return actual > target
        if node.op == 'less_than':
            return actual < target
    return False


def evaluate_node(node: Union[LeafCondition, GroupCondition], message) -> bool:
    """Evaluate a parsed condition node against a ``MailMessage`` row.

    Empty groups: ``all`` of nothing is ``True`` (vacuously true), ``any``
    of nothing is ``False`` (no witness). Standard logic identities.
    """
    if isinstance(node, LeafCondition):
        return _eval_leaf(node, message)
    if not node.conditions:
        return node.type == 'all'
    results = (evaluate_node(c, message) for c in node.conditions)
    if node.type == 'all':
        return all(results)
    return any(results)
