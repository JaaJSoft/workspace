"""A contact as a scannable card: the QR another phone's camera reads."""

from workspace.common.qr import QRTooLarge, make_qr

from .vcard import person_to_qr_vcard


def person_qr_code(person):
    """A ``segno.QRCode`` carrying the person's compact card.

    The notes are the one free-text field with no length of its own, so a
    card past what a QR holds is retried without them rather than refused;
    everything else is bounded by the columns it comes from. Raises
    ``QRTooLarge`` when even the trimmed card does not fit - a contact with
    a hundred phone numbers gets an honest error, not a silent half-card.
    """
    overflow = None
    for include_notes in (True, False):
        try:
            return make_qr(person_to_qr_vcard(person, include_notes=include_notes))
        except QRTooLarge as exc:
            overflow = exc
    raise overflow
