from workspace.common.url_guard import UnsafeUrl


class ImportsError(Exception):
    """Base of every failure the module reports to the user.

    ``user_message`` is always a string built by our own code (never a
    wrapped exception's text), so the API can return it verbatim.
    """

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


# What a connection check, a job or an endpoint reports to the user: the
# module's own failures and the URL guard's refusals, which the transport
# raises from inside any request to the remote. Both carry ``user_message``.
REPORTED_ERRORS = (ImportsError, UnsafeUrl)
