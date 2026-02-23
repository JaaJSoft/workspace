"""Build iCalendar (.ics) messages for outbound iTIP communication.

Currently supports METHOD:REPLY for responding to invitations.
"""

import icalendar


def build_reply(event, user, status):
    """Build a METHOD:REPLY .ics for accepting/declining an invitation.

    Parameters
    ----------
    event : Event
        The calendar event (must have ical_uid and organizer_email).
    user : User
        The user responding.
    status : str
        'accepted' or 'declined'.

    Returns
    -------
    bytes
        The .ics file content.
    """
    partstat = 'ACCEPTED' if status == 'accepted' else 'DECLINED'

    cal = icalendar.Calendar()
    cal.add('METHOD', 'REPLY')
    cal.add('PRODID', '-//Workspace//Calendar//EN')
    cal.add('VERSION', '2.0')

    vevent = icalendar.Event()
    vevent.add('UID', event.ical_uid)
    vevent.add('DTSTART', event.start)
    if event.end:
        vevent.add('DTEND', event.end)
    vevent.add('SUMMARY', event.title)
    vevent.add('SEQUENCE', event.ical_sequence)

    organizer = icalendar.vCalAddress(f'mailto:{event.organizer_email}')
    vevent.add('ORGANIZER', organizer)

    attendee = icalendar.vCalAddress(f'mailto:{user.email}')
    attendee.params['PARTSTAT'] = icalendar.vText(partstat)
    attendee.params['CN'] = icalendar.vText(user.get_full_name() or user.username)
    vevent.add('ATTENDEE', attendee)

    cal.add_component(vevent)

    return cal.to_ical()
