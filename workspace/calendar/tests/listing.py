"""The event list as the API serves it, read without a request."""

from workspace.calendar.services.event_listing import EventListing


def listed(user, start, end=None, *, limit=None, offset=0, tz=None, declined=False):
    """What ``GET /events`` answers *user*, in order.

    With *end*, the window, whole unless *limit* cuts it. Without one, the
    first *limit* events starting from *start* on. *declined* keeps the
    events the user declined, which the list leaves out otherwise.
    """
    narrow = (lambda events: events) if declined else None
    listing = EventListing(user, start=start, end=end, narrow=narrow, tz=tz)
    stop = None if limit is None else offset + limit
    return [row.data for row in listing[offset:stop]]
