class ImportsError(Exception):
    """Base of every failure the module reports to the user.

    ``user_message`` is always a string built by our own code (never a
    wrapped exception's text), so the API can return it verbatim.
    """

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message
