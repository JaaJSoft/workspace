"""IMAP connection helpers."""

import imaplib

# Default network timeout in seconds. Without it imaplib uses the system
# default, which can hang sync workers indefinitely when a server stops
# responding mid-handshake.
IMAP_TIMEOUT = 30


def connect_imap(account):
    """Open and authenticate an IMAP connection for the given account."""
    if account.imap_use_ssl:
        conn = imaplib.IMAP4_SSL(
            account.imap_host, account.imap_port, timeout=IMAP_TIMEOUT
        )
    else:
        conn = imaplib.IMAP4(account.imap_host, account.imap_port, timeout=IMAP_TIMEOUT)

    if account.auth_method == "oauth2":
        from workspace.mail.services.oauth2 import get_valid_access_token

        token = get_valid_access_token(account)
        if not token:
            # Without this guard we'd build "Bearer None" and the IMAP server
            # would reject it with an opaque AUTHENTICATIONFAILED.
            raise RuntimeError("No valid OAuth2 access token available")
        auth_string = f"user={account.username}\x01auth=Bearer {token}\x01\x01"
        conn.authenticate("XOAUTH2", lambda _: auth_string.encode())
    else:
        conn.login(account.username, account.get_password())
    return conn


def test_imap_connection(account):
    """Test IMAP connectivity. Returns (success, error_message)."""
    try:
        conn = connect_imap(account)
        conn.logout()
        return True, None
    except Exception as e:
        return False, str(e)
