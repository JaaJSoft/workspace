"""Mail module: OAuth2 providers.

Each provider is enabled by setting its CLIENT_ID + CLIENT_SECRET.
Only providers with a configured CLIENT_ID will appear in the UI.
"""

import os

# Google (Gmail / Google Workspace)
OAUTH_GOOGLE_CLIENT_ID = os.getenv("OAUTH_GOOGLE_CLIENT_ID", "")
OAUTH_GOOGLE_CLIENT_SECRET = os.getenv("OAUTH_GOOGLE_CLIENT_SECRET", "")

# Microsoft (Outlook / Office 365 / Hotmail)
OAUTH_MICROSOFT_CLIENT_ID = os.getenv("OAUTH_MICROSOFT_CLIENT_ID", "")
OAUTH_MICROSOFT_CLIENT_SECRET = os.getenv("OAUTH_MICROSOFT_CLIENT_SECRET", "")

# Generic OAuth2 provider (single custom provider, fully configurable)
OAUTH_GENERIC_CLIENT_ID = os.getenv("OAUTH_GENERIC_CLIENT_ID", "")
OAUTH_GENERIC_CLIENT_SECRET = os.getenv("OAUTH_GENERIC_CLIENT_SECRET", "")
OAUTH_GENERIC_NAME = os.getenv("OAUTH_GENERIC_NAME", "")
OAUTH_GENERIC_AUTH_URL = os.getenv("OAUTH_GENERIC_AUTH_URL", "")
OAUTH_GENERIC_TOKEN_URL = os.getenv("OAUTH_GENERIC_TOKEN_URL", "")
OAUTH_GENERIC_SCOPES = os.getenv("OAUTH_GENERIC_SCOPES", "")
OAUTH_GENERIC_IMAP_HOST = os.getenv("OAUTH_GENERIC_IMAP_HOST", "")
OAUTH_GENERIC_SMTP_HOST = os.getenv("OAUTH_GENERIC_SMTP_HOST", "")
