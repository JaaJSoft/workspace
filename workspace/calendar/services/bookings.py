"""Taking and cancelling an appointment on a booking page.

The appointment is an ordinary event on the page's calendar, with the guest
as an external attendee: the calendar is where the owner sees it, moves it or
deletes it. The ``Booking`` row is what the guest holds - their name, their
cancellation link - and follows the event: deleting the event, from any
client, cancels the booking (``cancel_with_event``).
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.utils.translation import pgettext

from workspace.common.uuids import uuid_v7_or_v4
from workspace.users.services.settings import get_user_timezone

from ..models import Booking, BookingPage, Event, EventMember
from ..models_booking import booking_ical_uid
from .booking_slots import is_bookable
from .notifications import notify_in_languages


class PageUnavailable(Exception):
    """The page was deactivated or deleted."""


class SlotUnavailable(Exception):
    """The slot is not, or no longer, free."""


def format_when(start, zone):
    """``start`` as a date and time a person reads in *zone*, in the active language."""
    # Translators: Django date format of a booked appointment, e.g. "Monday, March 2, 2026, 14:30".
    fmt = pgettext("booking date format", "l, F j, Y, H:i")
    return formats.date_format(start.astimezone(zone), fmt)


def book(
    page,
    *,
    start,
    guest_name,
    guest_email,
    note="",
    guest_timezone="",
    base_url="",
    now=None,
):
    """Take the slot at *start* on *page* for the guest.

    Every booking of one owner is serialized on the rows of their pages, so
    the slot is checked again once nobody else can be taking it: of two
    guests on the same slot, the second gets :class:`SlotUnavailable`.
    """
    duration = timedelta(minutes=page.duration_minutes)
    try:
        with transaction.atomic():
            locked = {
                p.uuid: p
                for p in BookingPage.objects.select_for_update(of=("self",))
                .select_related("owner")
                .filter(owner_id=page.owner_id)
                .order_by("uuid")
            }
            page = locked.get(page.uuid)
            if page is None or not page.is_active or not page.owner.is_active:
                raise PageUnavailable
            if not is_bookable(page, start, now=now):
                raise SlotUnavailable

            booking_uuid = uuid_v7_or_v4()
            event = Event.objects.create(
                calendar_id=page.calendar_id,
                owner_id=page.owner_id,
                title=f"{page.title} — {guest_name}",
                description=note,
                location=page.location,
                start=start,
                end=start + duration,
                timezone=page.timezone,
                source=Event.Source.BOOKING,
                ical_uid=booking_ical_uid(booking_uuid),
            )
            EventMember.objects.create(
                event=event,
                email=guest_email.strip().lower(),
                name=guest_name,
                status=EventMember.Status.ACCEPTED,
            )
            booking = Booking.objects.create(
                uuid=booking_uuid,
                page=page,
                start=start,
                end=start + duration,
                guest_name=guest_name,
                guest_email=guest_email.strip(),
                note=note,
                timezone=guest_timezone,
                event=event,
            )
            _notify_owner_booked(booking, event)
            _send_mail_on_commit(booking, "confirmed", base_url)
    except IntegrityError:
        raise SlotUnavailable from None
    return booking


def cancel(booking, *, by):
    """Cancel *booking* and remove its event; False when it was already cancelled.

    The guest is mailed a cancellation whoever cancelled, so their calendar
    copy goes away too. The owner is notified when the guest cancelled.
    """
    with transaction.atomic():
        updated = Booking.objects.filter(
            pk=booking.pk, status=Booking.Status.CONFIRMED
        ).update(
            status=Booking.Status.CANCELLED,
            cancelled_by=by,
            cancelled_at=timezone.now(),
        )
        if not updated:
            return False
        booking.refresh_from_db()
        if by == Booking.CancelledBy.GUEST:
            _notify_owner_cancelled(booking)
        _send_mail_on_commit(booking, "cancelled", "")
        if booking.event_id:
            Event.objects.filter(pk=booking.event_id).delete()
    return True


def _notify_owner_booked(booking, event):
    page = booking.page
    zone = get_user_timezone(page.owner)

    def message():
        return (
            _('New booking: "%(title)s"') % {"title": page.title},
            _("%(guest)s booked %(when)s.")
            % {"guest": booking.guest_name, "when": format_when(booking.start, zone)},
        )

    notify_in_languages(
        recipients=[page.owner],
        message=message,
        url=f"/calendar?event={event.pk}",
        source=event,
    )


def _notify_owner_cancelled(booking):
    page = booking.page
    zone = get_user_timezone(page.owner)

    def message():
        return (
            _('Booking cancelled: "%(title)s"') % {"title": page.title},
            _("%(guest)s cancelled %(when)s.")
            % {"guest": booking.guest_name, "when": format_when(booking.start, zone)},
        )

    notify_in_languages(recipients=[page.owner], message=message, url="/calendar")


def _send_mail_on_commit(booking, kind, base_url):
    if booking.page.mail_account_id is None:
        return
    from ..tasks import send_booking_mail

    booking_id = str(booking.pk)
    transaction.on_commit(lambda: send_booking_mail.delay(booking_id, kind, base_url))


@receiver(pre_delete, sender=Event, dispatch_uid="calendar.booking_event_deleted")
def cancel_with_event(sender, instance, **kwargs):
    """Deleting a booked event cancels the appointment, whichever client deleted it."""
    if instance.source != Event.Source.BOOKING:
        return
    for booking in Booking.objects.filter(
        event_id=instance.pk, status=Booking.Status.CONFIRMED
    ).select_related("page"):
        Booking.objects.filter(pk=booking.pk).update(
            status=Booking.Status.CANCELLED,
            cancelled_by=Booking.CancelledBy.OWNER,
            cancelled_at=timezone.now(),
        )
        _send_mail_on_commit(booking, "cancelled", "")


@receiver(post_save, sender=Event, dispatch_uid="calendar.booking_event_moved")
def follow_moved_event(sender, instance, created, **kwargs):
    """A booked event moved in the calendar takes its booking along."""
    if (
        created
        or instance.source != Event.Source.BOOKING
        or instance.all_day
        or instance.end is None
    ):
        return
    try:
        # The owner may stack two appointments of one page on the same time;
        # the booking then keeps its old times rather than failing the move
        # on the one-booking-per-slot constraint.
        with transaction.atomic():
            Booking.objects.filter(
                event_id=instance.pk, status=Booking.Status.CONFIRMED
            ).exclude(start=instance.start, end=instance.end).update(
                start=instance.start, end=instance.end
            )
    except IntegrityError:
        pass
