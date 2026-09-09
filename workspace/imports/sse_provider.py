from workspace.core.sse_registry import MailboxSSEProvider

from .services.progress import SLUG


class ImportsSSEProvider(MailboxSSEProvider):
    slug = SLUG
