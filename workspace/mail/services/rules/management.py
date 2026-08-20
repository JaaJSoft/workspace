"""Bulk management of mail rules, as opposed to evaluating them (engine)."""

from django.utils import timezone


def set_rules_enabled(rules, enabled):
    """Enable or disable *rules* in bulk; returns the number of rows updated.

    ``updated_at`` is stamped by hand: ``QuerySet.update`` bypasses
    ``auto_now``.
    """
    return rules.update(is_enabled=enabled, updated_at=timezone.now())
