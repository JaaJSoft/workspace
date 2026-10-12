"""Read a capture date from a file name, for the photos and videos whose
metadata carries none.

Phones, messaging apps and screenshot tools name their files after the moment
they were taken, and people who scan prints name them after the day of the
picture by hand. Among the names read:

- ``IMG_20230415_143022.jpg``, ``VID_20230415_143022.mp4`` (Android cameras),
  ``20230415_143022.jpg`` (Samsung), ``IMG20230415143022.jpg`` (Oppo)
- ``PXL_20230415_143022123.jpg`` (Pixel, in UTC: see ``_UTC_PREFIXES``)
- ``IMG-20230415-WA0001.jpg`` (WhatsApp, the day only)
- ``WhatsApp Image 2023-04-15 at 14.30.22.jpeg``
- ``signal-2023-04-15-143022.jpg``, ``signal-2023-04-15-14-30-22-123.jpg``
- ``Screenshot_20230415-143022.png``, ``Screenshot 2023-04-15 at 14.30.22.png``,
  ``Screen Shot 2020-01-02 at 3.04.05 PM.png`` (macOS)
- ``2023-04-15 14.30.22.jpg`` (Dropbox camera uploads)
- ``2023-04-15T14:30:22Z.jpg``, ``2023-04-15 14h30 Anniversaire.jpg``,
  ``1998-07-12 Mariage.jpg``

The date may sit anywhere in the name (``TOTO_2023-04-15.png``): the
prefixes above are examples, not a list the name is matched against.

The year always comes first. A name that puts the day first (``15-04-2023``)
cannot be told apart from one that puts the month first, and is not read.
"""

import re
from datetime import UTC, datetime, timedelta

from django.utils import timezone

_DATE_RE = re.compile(
    r"""
    (?<!\d)
    (?P<year>(?:19|20)\d{2}) (?P<date_sep>[-_. ]?) (?P<month>\d{2}) (?P=date_sep) (?P<day>\d{2})
    (?:
        (?:\s+at\s+|[T_\- ]|)
        (?:
            (?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})
          | (?P<hour_s>\d{1,2}) (?P<time_sep>[-_.:h]) (?P<minute_s>\d{2})
            (?:(?P=time_sep) (?P<second_s>\d{2}))?
        )
        # Milliseconds (Pixel, Signal) or a burst counter: read past, ignored.
        (?:[.,_-]?\d{1,9})?
        (?:\s?(?P<meridiem>[AaPp][Mm])\b)?
        (?:(?P<utc>Z)(?![A-Za-z]))?
    )?
    (?!\d)
    """,
    re.VERBOSE,
)

# Pixel phones name their pictures after the UTC time, not the local one.
_UTC_PREFIXES = ("PXL_",)

# A name giving the day only reads as noon on that day, which stays on that
# day when shown in any timezone within twelve hours of the owner's.
_DAY_ONLY_HOUR = 12

# A date past now is a version number or a deadline, not a capture date. The
# slack covers a name written in a timezone ahead of the server's clock.
_CLOCK_SLACK = timedelta(days=1)


def date_from_filename(name, *, default_tz=UTC, now=None):
    """The capture time written in *name*, or None.

    *default_tz* reads the time, like an EXIF date without an offset: a name
    carries the wall clock of the device that wrote it, and the owner's
    timezone is the best guess at where that clock was.
    """
    latest = (now or timezone.now()) + _CLOCK_SLACK
    tz = UTC if name.upper().startswith(_UTC_PREFIXES) else default_tz
    for match in _DATE_RE.finditer(name):
        moment = _moment(match, tz)
        if moment is not None and moment <= latest:
            return moment
    return None


def _moment(match, tz):
    hour = match["hour"] or match["hour_s"]
    if hour is None:
        hour, minute, second = _DAY_ONLY_HOUR, 0, 0
    else:
        hour = int(hour)
        minute = int(match["minute"] or match["minute_s"])
        second = int(match["second"] or match["second_s"] or 0)
    if match["meridiem"]:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if match["meridiem"].lower() == "pm" else 0)
    try:
        naive = datetime(
            int(match["year"]),
            int(match["month"]),
            int(match["day"]),
            hour,
            minute,
            second,
        )
    except ValueError:
        return None
    return timezone.make_aware(naive, UTC if match["utc"] else tz)
