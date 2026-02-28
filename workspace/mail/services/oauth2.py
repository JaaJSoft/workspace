"""OAuth2 helpers for mail account authentication.

Supports Google, Microsoft, and a single generic (admin-configured) provider.
Uses authlib for the OAuth2 handshake and token refresh.
"""

import time

from authlib.integrations.requests_client import OAuth2Session
from django.conf import settings

# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

GOOGLE_CONFIG = {
    'name': 'Google',
    'auth_url': 'https://accounts.google.com/o/oauth2/v2/auth',
    'token_url': 'https://oauth2.googleapis.com/token',
    'userinfo_url': 'https://openidconnect.googleapis.com/v1/userinfo',
    'scopes': 'https://mail.google.com/ openid email',
    'imap_host': 'imap.gmail.com',
    'imap_port': 993,
    'imap_use_ssl': True,
    'smtp_host': 'smtp.gmail.com',
    'smtp_port': 587,
    'smtp_use_tls': True,
}

MICROSOFT_CONFIG = {
    'name': 'Microsoft',
    'auth_url': 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    'token_url': 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
    'userinfo_url': 'https://graph.microsoft.com/v1.0/me',
    'scopes': (
        'https://outlook.office365.com/IMAP.AccessAsUser.All '
        'https://outlook.office365.com/SMTP.Send '
        'offline_access openid email'
    ),
    'imap_host': 'outlook.office365.com',
    'imap_port': 993,
    'imap_use_ssl': True,
    'smtp_host': 'smtp.office365.com',
    'smtp_port': 587,
    'smtp_use_tls': True,
}


def _get_generic_config():
    """Build a provider config dict from OAUTH_GENERIC_* Django settings."""
    name = getattr(settings, 'OAUTH_GENERIC_NAME', '') or 'Generic'
    return {
        'name': name,
        'auth_url': getattr(settings, 'OAUTH_GENERIC_AUTH_URL', ''),
        'token_url': getattr(settings, 'OAUTH_GENERIC_TOKEN_URL', ''),
        'userinfo_url': '',
        'scopes': getattr(settings, 'OAUTH_GENERIC_SCOPES', ''),
        'imap_host': getattr(settings, 'OAUTH_GENERIC_IMAP_HOST', ''),
        'imap_port': 993,
        'imap_use_ssl': True,
        'smtp_host': getattr(settings, 'OAUTH_GENERIC_SMTP_HOST', ''),
        'smtp_port': 587,
        'smtp_use_tls': True,
    }


def get_provider_config(provider):
    """Return the config dict for *provider* or ``None`` if unknown."""
    if provider == 'google':
        return GOOGLE_CONFIG
    if provider == 'microsoft':
        return MICROSOFT_CONFIG
    if provider == 'generic':
        return _get_generic_config()
    return None


def _get_client_credentials(provider):
    """Return ``(client_id, client_secret)`` for the given provider."""
    provider_upper = provider.upper()
    client_id = getattr(settings, f'OAUTH_{provider_upper}_CLIENT_ID', '')
    client_secret = getattr(settings, f'OAUTH_{provider_upper}_CLIENT_SECRET', '')
    return client_id, client_secret


def get_available_providers():
    """Return a list of provider dicts whose CLIENT_ID is configured.

    Each entry has ``{'provider': ..., 'name': ...}``.
    """
    providers = []
    for provider_id in ('google', 'microsoft', 'generic'):
        client_id, _ = _get_client_credentials(provider_id)
        if client_id:
            config = get_provider_config(provider_id)
            providers.append({'provider': provider_id, 'name': config['name']})
    return providers


# ---------------------------------------------------------------------------
# OAuth2 flow helpers
# ---------------------------------------------------------------------------

def build_authorize_url(provider, callback_url):
    """Generate the OAuth2 authorization URL for the given provider.

    Returns ``(url, state)`` where *state* should be stored in the session.
    """
    config = get_provider_config(provider)
    client_id, _ = _get_client_credentials(provider)

    session = OAuth2Session(
        client_id=client_id,
        scope=config['scopes'],
        redirect_uri=callback_url,
    )

    # Provider-specific params required for refresh tokens
    extra = {}
    if provider == 'google':
        extra['access_type'] = 'offline'
        extra['prompt'] = 'consent'

    url, state = session.create_authorization_url(config['auth_url'], **extra)
    return url, state


def exchange_code(provider, code, callback_url):
    """Exchange an authorization *code* for tokens.

    Returns the token dict (access_token, refresh_token, expires_at, ...).
    """
    config = get_provider_config(provider)
    client_id, client_secret = _get_client_credentials(provider)

    session = OAuth2Session(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=callback_url,
    )
    token = session.fetch_token(
        config['token_url'],
        grant_type='authorization_code',
        code=code,
    )
    return token


def fetch_userinfo(provider, access_token):
    """Fetch the user's email address from the provider's userinfo endpoint.

    Returns a dict with at least an ``'email'`` key, or ``{}`` if the
    endpoint is not configured.
    """
    config = get_provider_config(provider)
    url = config.get('userinfo_url')
    if not url:
        return {}

    session = OAuth2Session(token={'access_token': access_token, 'token_type': 'Bearer'})
    resp = session.get(url)
    resp.raise_for_status()
    data = resp.json()

    # Microsoft Graph returns 'mail' instead of 'email'
    if provider == 'microsoft' and 'email' not in data:
        data['email'] = data.get('mail', data.get('userPrincipalName', ''))

    return data


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def get_valid_access_token(account):
    """Return a valid access token for *account*, refreshing if necessary.

    Uses a 60-second buffer so tokens are refreshed slightly before expiry.
    """
    data = account.get_oauth2_data()
    if data is None:
        return None

    expires_at = data.get('expires_at', 0)
    if time.time() < (expires_at - 60):
        return data['access_token']

    # Token is expired (or about to be) -- refresh it.
    return _refresh_token(account, data)


def _refresh_token(account, data):
    """Refresh the OAuth2 token and persist the updated data.

    Returns the new access token string, or raises if no refresh_token
    is available.
    """
    refresh_tok = data.get('refresh_token')
    if not refresh_tok:
        raise RuntimeError(
            f"No refresh_token for {account.email} — re-authorize the account"
        )

    provider = account.oauth2_provider
    config = get_provider_config(provider)
    client_id, client_secret = _get_client_credentials(provider)

    session = OAuth2Session(
        client_id=client_id,
        client_secret=client_secret,
    )
    new_token = session.fetch_token(
        config['token_url'],
        grant_type='refresh_token',
        refresh_token=refresh_tok,
    )

    # Merge: keep existing refresh_token if the provider did not issue a new one
    if 'refresh_token' not in new_token:
        new_token['refresh_token'] = refresh_tok

    account.set_oauth2_data(new_token)
    account.save(update_fields=['oauth2_data_encrypted'])

    return new_token['access_token']
