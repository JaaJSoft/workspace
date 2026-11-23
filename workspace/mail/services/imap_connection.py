"""IMAP connections: opening one, sharing it, and what IMAPClient leaves out."""

import contextlib
import contextvars
import mmap

from django.utils import timezone
from imapclient import IMAPClient
from imapclient.datetime_util import datetime_to_INTERNALDATE
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError, ProtocolError
from imapclient.response_parser import parse_response

from workspace.common.url_guard import UnsafeUrl
from workspace.mail.services import tls
from workspace.mail.services.network_policy import check_mail_host

# Default network timeout in seconds, for the connection and for every read
# after it. Without one a server that stops responding mid-handshake hangs a
# sync worker indefinitely.
IMAP_TIMEOUT = 30

# What a dead connection raises: the server hung up, the socket timed out or
# broke, or the answer made no sense. A refused command (a plain
# IMAPClientError) leaves the connection usable and is not one of them.
ConnectionLost = (IMAPClientAbortError, ProtocolError, OSError)


def connect_imap(account):
    """Open and authenticate an IMAP connection for the given account.

    ``imap_use_ssl`` picks implicit TLS (on) or STARTTLS (off). There is no
    connection in clear: ``starttls()`` raises when the server does not offer
    it, before any credential is sent. Both verify the server's certificate.
    """
    check_mail_host(account.imap_host)
    context = tls.client_context()
    client = IMAPClient(
        account.imap_host,
        account.imap_port,
        ssl=account.imap_use_ssl,
        ssl_context=context,
        timeout=IMAP_TIMEOUT,
    )
    try:
        if not account.imap_use_ssl:
            client.starttls(ssl_context=context)
        # Mailbox names are stored the way the server spells them (modified
        # UTF-7) and go back as they came: see imap_mailbox.
        client.folder_encode = False
        if account.auth_method == "oauth2":
            from workspace.mail.services.oauth2 import get_valid_access_token

            token = get_valid_access_token(account)
            if not token:
                # Without this guard we'd send "Bearer None" and the server
                # would reject it with an opaque AUTHENTICATIONFAILED.
                raise RuntimeError("No valid OAuth2 access token available")
            client.oauth2_login(account.username, token)
        else:
            client.login(account.username, account.get_password())
        # From here on every SELECT carries the mailbox's HIGHESTMODSEQ,
        # which is how the sync tells a folder nothing touched (RFC 7162).
        if client.has_capability("ENABLE") and client.has_capability("CONDSTORE"):
            client.enable("CONDSTORE")
    except BaseException:
        _close(client, logout=False)
        raise
    return client


def _close(client, logout=True):
    """Best-effort: failing to close an already-broken connection isn't
    actionable."""
    with contextlib.suppress(Exception):
        if logout:
            client.logout()
            return
    with contextlib.suppress(Exception):
        client.shutdown()


class _Session:
    client = None


# The sessions of the innermost `shared_connections` block, by account.
_sessions = contextvars.ContextVar("mail_imap_sessions", default=None)


@contextlib.contextmanager
def shared_connections():
    """Keep the connection of every account used inside the block open
    until the block ends.

    For a loop over messages (a bulk action, a rule applied to a folder):
    each iteration calls services written as if they opened a connection of
    their own, and inside this block they find the one a previous iteration
    left. Nothing is opened before a service needs it, so an account whose
    server is down fails message by message, as it would without the block.
    """
    sessions = _sessions.get()
    if sessions is not None:
        yield sessions
        return

    sessions = {}
    token = _sessions.set(sessions)
    try:
        yield sessions
    finally:
        _sessions.reset(token)
        for session in sessions.values():
            if session.client is not None:
                _close(session.client)


@contextlib.contextmanager
def imap_session(account):
    """The connection to `account`, for the commands of one operation.

    Opened here unless an enclosing block already holds one for the account
    - another `imap_session`, or `shared_connections` - and closed when the
    outermost of them ends. A sync pass wraps itself in one, so its folders
    and the rules it runs on new messages share a connection.

    Two consequences for the code inside. The selected mailbox is whatever
    the last user of the connection left: select before every command that
    needs one. And the connection only outlives an error known to leave the
    conversation in order, a command the server refused. Anything else may
    have cut a command short (a lost connection, a task's time limit
    falling in the middle of a FETCH), with an answer still on its way that
    the next command would take for its own: the connection is dropped, and
    the next block opens a fresh one.
    """
    with shared_connections() as sessions:
        session = sessions.setdefault(account.pk, _Session())
        if session.client is None:
            session.client = connect_imap(account)
        try:
            yield session.client
        except BaseException as error:
            refused = isinstance(error, IMAPClientError) and not isinstance(
                error, ConnectionLost
            )
            if not refused and session.client is not None:
                _close(session.client, logout=False)
                session.client = None
            raise


def test_imap_connection(account):
    """Test IMAP connectivity. Returns (success, error_message); a server
    mail may not connect to raises ``UnsafeUrl`` instead."""
    try:
        _close(connect_imap(account))
        return True, None
    except UnsafeUrl:
        raise
    except Exception as e:
        return False, str(e)


# IMAPClient is a layer over the standard library's imaplib and leaves two
# things this module needs to it. Both reach the imaplib connection it wraps
# (``client._imap``); the live IMAP tests fail if an upgrade moves it.


def last_response_code(client, name):
    """The fields of the last ``[name ...]`` response code, or None.

    IMAPClient hands back the text of a tagged response and parses none of
    its codes (COPYUID, APPENDUID). imaplib files each one under its name.
    Reading a code consumes it: read once before the command to drop a stale
    one, once after to get this command's.
    """
    _name, data = client._imap.response(name)
    if not data or not data[-1]:
        return None
    try:
        return parse_response([data[-1]])
    except ProtocolError, ValueError:
        return None


def append_file(client, folder, flags, message_file):
    """APPEND the message held in `message_file` to `folder`.

    `IMAPClient.append` wants the message as bytes and copies it again to
    normalize its line endings, then to frame it. A sent message carries its
    attachments and can weigh hundreds of MB, in a web worker. A memory map
    of the file, already CRLF, goes out as the literal instead: sent from
    the page cache, never copied to the heap.
    """
    message_file.flush()
    imap = client._imap
    with mmap.mmap(message_file.fileno(), 0, access=mmap.ACCESS_READ) as literal:
        imap.literal = literal
        status, data = imap._simple_command(
            "APPEND",
            client._normalise_folder(folder),
            f"({' '.join(flags)})",
            f'"{datetime_to_INTERNALDATE(timezone.now())}"',
        )
    if status != "OK":
        raise IMAPClientError(f"append failed: {data[-1]!r}")
