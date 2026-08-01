"""Build iCalendar (.ics) messages for outbound iTIP communication.

Currently supports METHOD:REPLY for responding to invitations.
"""

from datetime import UTC

import icalendar

from workspace.calendar.services.timezones import event_timezone


def _add_event_times(vevent, event):
    """Emit DTSTART/DTEND per RFC 5545 semantics.

    All-day events are VALUE=DATE day labels; zoned events are emitted as
    wall-clock local times with a TZID parameter (icalendar sets it from
    the tzinfo key); legacy events stay UTC instants.
    """
    if event.all_day:
        vevent.add("DTSTART", event.start.astimezone(UTC).date())
        if event.end:
            vevent.add("DTEND", event.end.astimezone(UTC).date())
        return
    tz = event_timezone(event)
    vevent.add("DTSTART", event.start.astimezone(tz) if tz else event.start)
    if event.end:
        vevent.add("DTEND", event.end.astimezone(tz) if tz else event.end)


def build_reply(event, user, status):
    """Build a METHOD:REPLY .ics for accepting/declining an invitation.

    Parameters
    ----------
    event : Event
        The calendar event (must have ical_uid and external_organizer).
    user : User
        The user responding.
    status : str
        'accepted' or 'declined'.

    Returns
    -------
    bytes
        The .ics file content.
    """
    partstat = "ACCEPTED" if status == "accepted" else "DECLINED"

    cal = icalendar.Calendar()
    cal.add("METHOD", "REPLY")
    cal.add("PRODID", "-//Workspace//Calendar//EN")
    cal.add("VERSION", "2.0")

    vevent = icalendar.Event()
    vevent.add("UID", event.ical_uid)
    _add_event_times(vevent, event)
    vevent.add("SUMMARY", event.title)
    vevent.add("SEQUENCE", event.ical_sequence)

    organizer = icalendar.vCalAddress(f"mailto:{event.external_organizer}")
    vevent.add("ORGANIZER", organizer)

    attendee = icalendar.vCalAddress(f"mailto:{user.email}")
    attendee.params["PARTSTAT"] = icalendar.vText(partstat)
    attendee.params["CN"] = icalendar.vText(user.get_full_name() or user.username)
    vevent.add("ATTENDEE", attendee)

    cal.add_component(vevent)
    # Interop: recipients need the VTIMEZONE definitions for any TZID we
    # reference above.
    cal.add_missing_timezones()

    return cal.to_ical()
