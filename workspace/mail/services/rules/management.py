"""Bulk management of mail rules, as opposed to evaluating them (engine)."""


def set_rules_enabled(rules, enabled):
    """Enable or disable *rules* in bulk; returns the number of rows updated."""
    return rules.update(is_enabled=enabled)
