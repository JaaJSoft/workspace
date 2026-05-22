from workspace.users.services.settings import get_setting

MAIL_AI_FEATURES = ('classify', 'extract', 'manual')


def is_mail_ai_feature_enabled(user, feature: str) -> bool:
    if feature not in MAIL_AI_FEATURES:
        raise ValueError(f'Unknown mail AI feature: {feature!r}')
    # Fall back to the legacy single ai_enabled toggle so users who turned
    # everything off before the split don't see features silently re-enable.
    legacy_default = get_setting(user, 'mail', 'ai_enabled', default=True)
    return bool(get_setting(user, 'mail', f'ai_{feature}', default=legacy_default))
