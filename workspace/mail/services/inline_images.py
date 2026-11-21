"""Pictures an HTML body embeds from its own message (RFC 2392 ``cid:``).

The stored body keeps its ``cid:`` references. They are resolved when the
message is read, against that message's attachments only: an id that names
none of them is dropped, so a body can never point at another message's part.
"""

from urllib.parse import unquote

from django.urls import reverse


def _content_id(reference):
    """The Content-ID a ``cid:`` URL names: percent-decoded, no brackets."""
    return unquote(reference[4:]).strip().strip("<>").strip()


def is_cid(value):
    return value[:4].lower() == "cid:"


class InlineImages:
    """Maps the ``cid:`` references of one message to its stored attachments.

    ``resolve`` is an nh3 ``attribute_filter``. An image source becomes the
    attachment's download URL, and any other ``cid:`` reference (an unknown
    id, a link) is dropped. The attachments it resolved end up in
    ``embedded``, by UUID.
    """

    def __init__(self, attachments):
        self._by_content_id = {}
        for attachment in attachments:
            content_id = attachment.content_id.strip()
            if content_id:
                self._by_content_id.setdefault(content_id, attachment)
        self.embedded = set()

    def resolve(self, tag, attr, value):
        if not is_cid(value):
            return value
        if tag != "img" or attr != "src":
            return None
        attachment = self._by_content_id.get(_content_id(value))
        if attachment is None:
            return None
        self.embedded.add(attachment.uuid)
        return reverse("mail-attachment-download", kwargs={"uuid": attachment.uuid})
