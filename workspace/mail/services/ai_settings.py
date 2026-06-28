from workspace.users.services.settings import get_module_settings

MAIL_AI_FEATURES = ("classify", "extract", "manual")


def is_mail_ai_feature_enabled(user, feature: str) -> bool:
    if feature not in MAIL_AI_FEATURES:
        raise ValueError(f"Unknown mail AI feature: {feature!r}")
    # Per-feature flags live in the mail module; a single read serves repeated
    # checks via the cache (imap_sync probes classify and extract back to back).
    mail_settings = get_module_settings(user, "mail")
    return bool(mail_settings.get(f"ai_{feature}", True))
