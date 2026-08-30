from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .test_calendar import CalendarTestMixin


class EventIsRecurringShimTests(CalendarTestMixin, TestCase):
    """`Event.save()` mirrors `is_recurring` from `recurrence_frequency` while
    `recurrence_rule` is blank (see the shim's docstring in models.py). The
    mirrored value must reach the database even when the caller restricts
    `update_fields` to a column that isn't `is_recurring`.
    """

    def test_update_fields_save_still_persists_the_mirrored_value(self):
        event = self.event
        self.assertFalse(event.is_recurring)

        event.recurrence_frequency = "weekly"
        event.end = event.end + timedelta(hours=1)
        event.save(update_fields=["end"])

        reloaded = type(event).objects.get(pk=event.pk)
        self.assertTrue(reloaded.is_recurring)
