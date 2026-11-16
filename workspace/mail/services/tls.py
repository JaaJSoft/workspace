"""TLS settings of the connections to mail servers."""

import ssl


def client_context():
    """A context that verifies the server's certificate and host name.

    imaplib and smtplib default to ``ssl._create_stdlib_context``, which checks
    neither, so any certificate would do and a man in the middle could read
    the credentials. Trust comes from the system store: a server signed by a
    private CA is accepted once that CA is installed there, or named by
    ``SSL_CERT_FILE``.
    """
    return ssl.create_default_context()
