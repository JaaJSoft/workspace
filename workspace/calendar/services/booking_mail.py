"""The mails a booking guest receives: the confirmation and the cancellation.

Both carry an iTIP part (METHOD:REQUEST, then METHOD:CANCEL under the same
UID) so the guest's calendar client adds the appointment, then removes it.
They leave from the page's mail account - the instance has no mail server of
its own - in the owner's language, the one the booking page is shown in.
"""

from datetime import UTC
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import icalendar
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from workspace.users.services.settings import get_user_language

from ..models import Booking
from .booking_slots import page_zone
from .bookings import format_when

CONFIRMED = "confirmed"
CANCELLED = "cancelled"
# The copy a guest downloads from their booking link: an event for their own
# calendar, with nobody to answer to.
PUBLISHED = "published"

_METHODS = {CONFIRMED: "REQUEST", CANCELLED: "CANCEL", PUBLISHED: "PUBLISH"}


def owner_display_name(user):
    return user.get_full_name() or user.username


def guest_zone(booking):
    """The zone the guest browsed the page in, or the page's own."""
    if booking.timezone:
        try:
            return ZoneInfo(booking.timezone)
        except ZoneInfoNotFoundError, ValueError, KeyError:
            pass
    return page_zone(booking.page)


def build_ics(booking, kind, organizer_email=""):
    """The iTIP object of the appointment, as the guest's calendar reads it."""
    page = booking.page
    cancelled = kind == CANCELLED

    cal = icalendar.Calendar()
    cal.add("PRODID", "-//Workspace//Calendar//EN")
    cal.add("VERSION", "2.0")
    cal.add("METHOD", _METHODS[kind])

    vevent = icalendar.Event()
    vevent.add("UID", booking.ical_uid)
    vevent.add("DTSTAMP", timezone.now().astimezone(UTC))
    zone = page_zone(page)
    vevent.add("DTSTART", booking.start.astimezone(zone))
    vevent.add("DTEND", booking.end.astimezone(zone))
    vevent.add("SUMMARY", page.title)
    if page.description:
        vevent.add("DESCRIPTION", page.description)
    if page.location:
        vevent.add("LOCATION", page.location)
    vevent.add("SEQUENCE", 1 if cancelled else 0)
    vevent.add("STATUS", "CANCELLED" if cancelled else "CONFIRMED")

    if kind != PUBLISHED:
        organizer = icalendar.vCalAddress(f"mailto:{organizer_email}")
        organizer.params["CN"] = icalendar.vText(owner_display_name(page.owner))
        vevent.add("ORGANIZER", organizer)

        attendee = icalendar.vCalAddress(f"mailto:{booking.guest_email}")
        attendee.params["CN"] = icalendar.vText(booking.guest_name)
        attendee.params["ROLE"] = icalendar.vText("REQ-PARTICIPANT")
        attendee.params["PARTSTAT"] = icalendar.vText("ACCEPTED")
        vevent.add("ATTENDEE", attendee)

    cal.add_component(vevent)
    cal.add_missing_timezones()
    return cal.to_ical()


def _body(booking, kind, manage_url):
    page = booking.page
    zone = guest_zone(booking)
    when = format_when(booking.start, zone)
    lines = [_("Hello %(name)s,") % {"name": booking.guest_name}, ""]
    if kind == CANCELLED:
        lines.append(
            _("Your appointment on %(when)s has been cancelled.") % {"when": when}
        )
        lines += ["", page.title]
    else:
        lines.append(_("Your appointment is confirmed."))
        lines += ["", page.title]
        lines.append(_("When: %(when)s (%(zone)s)") % {"when": when, "zone": zone.key})
    lines.append(_("With: %(name)s") % {"name": owner_display_name(page.owner)})
    if page.location and kind != CANCELLED:
        lines.append(_("Where: %(location)s") % {"location": page.location})
    if manage_url and kind != CANCELLED:
        lines += ["", _("To cancel it, open this link: %(url)s") % {"url": manage_url}]
    return "\n".join(lines) + "\n"


def build_message(booking, kind, base_url=""):
    """The MIME message mailing *booking* to its guest, ready to send."""
    page = booking.page
    account = page.mail_account
    manage_url = ""
    if base_url:
        manage_url = base_url.rstrip("/") + reverse(
            "calendar_ui:booking-manage", args=[booking.token]
        )

    with translation.override(get_user_language(page.owner)):
        when = format_when(booking.start, guest_zone(booking))
        if kind == CANCELLED:
            subject = _("Cancelled: %(title)s, %(when)s") % {
                "title": page.title,
                "when": when,
            }
        else:
            subject = _("Confirmed: %(title)s, %(when)s") % {
                "title": page.title,
                "when": when,
            }
        body = _body(booking, kind, manage_url)

    msg = MIMEMultipart("mixed")
    msg["From"] = formataddr((owner_display_name(page.owner), account.email), "utf-8")
    msg["To"] = formataddr((booking.guest_name, booking.guest_email), "utf-8")
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=account.email.split("@")[-1])
    msg.attach(MIMEText(body, "plain", "utf-8"))

    calendar_part = MIMEText(
        build_ics(booking, kind, account.email).decode("utf-8"), "calendar", "utf-8"
    )
    calendar_part.set_param("method", _METHODS[kind])
    msg.attach(calendar_part)
    return msg


def booking_for_mail(booking_id, kind):
    """The booking with what a mail needs, or None when it cannot be mailed.

    The mail is queued when the booking is taken or cancelled and sent
    later: a confirmation is dropped once the booking was cancelled since,
    and nothing leaves for an owner who was deactivated meanwhile.
    """
    booking = (
        Booking.objects.select_related("page__owner", "page__mail_account")
        .filter(pk=booking_id)
        .first()
    )
    if booking is None or booking.page.mail_account is None:
        return None
    page = booking.page
    if not page.owner.is_active or page.mail_account.owner_id != page.owner_id:
        return None
    if kind == CONFIRMED and booking.status != Booking.Status.CONFIRMED:
        return None
    return booking
