from workspace.users.services.settings import get_module_settings

MAIL_AI_FEATURES = ("classify", "extract", "manual")


def is_mail_ai_feature_enabled(user, feature: str) -> bool:
    if feature not in MAIL_AI_FEATURES:
        raise ValueError(f"Unknown mail AI feature: {feature!r}")
    # Fall back to the legacy single ai_enabled toggle so users who turned
    # everything off before the split don't see features silently re-enable.
    # The legacy flag and the per-feature flag both live in the mail module,
    # so read them in a single query (shared across repeated checks via the
    # cache - imap_sync probes classify and extract back to back).
    mail_settings = get_module_settings(user, "mail")
    legacy_default = mail_settings.get("ai_enabled", True)
    return bool(mail_settings.get(f"ai_{feature}", legacy_default))
