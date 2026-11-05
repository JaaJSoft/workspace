"""Booking pages: a public link where anyone picks a slot of the owner's real
availability, and the appointment lands on the owner's calendar.

A page states its rules once (duration, weekly hours, notice, horizon,
buffer); the free slots are computed on every request from those rules and
the owner's events (``services.booking_slots``), never stored.
"""

import secrets

from django.conf import settings
from django.db import models

from workspace.common.uuids import uuid_v7_or_v4

from .models import Calendar, Event


def _generate_page_token():
    return secrets.token_urlsafe(12)


def _generate_booking_token():
    return secrets.token_urlsafe(24)


def booking_ical_uid(booking_uuid):
    """UID of an appointment, shared by the owner's event and the guest's copy."""
    return f"booking-{booking_uuid}"


def default_weekly_hours():
    """Monday to Friday, 9:00 to 17:00."""
    return [[["09:00", "17:00"]] for _ in range(5)] + [[], []]


class BookingPage(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="booking_pages",
    )
    # Where booked appointments land. Always checked for conflicts, on top of
    # ``busy_calendars``.
    calendar = models.ForeignKey(
        Calendar,
        on_delete=models.CASCADE,
        related_name="booking_pages",
    )
    busy_calendars = models.ManyToManyField(Calendar, related_name="+", blank=True)
    # The public link. Random rather than chosen: no two users of the
    # instance compete for a name, and nobody can enumerate the pages.
    token = models.CharField(max_length=32, unique=True, default=_generate_page_token)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    location = models.CharField(max_length=255, blank=True, default="")
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    # Free time kept on both sides of an appointment.
    buffer_minutes = models.PositiveSmallIntegerField(default=0)
    min_notice_hours = models.PositiveSmallIntegerField(default=4)
    days_ahead = models.PositiveSmallIntegerField(default=30)
    # Seven lists, Monday first, of ["HH:MM", "HH:MM"] wall-clock ranges in
    # ``timezone``. Validated by BookingPageSerializer.
    weekly_hours = models.JSONField(default=default_weekly_hours)
    timezone = models.CharField(max_length=64)
    # The account confirmations go out from. The instance has no mail server
    # of its own, so without one the guest only gets the confirmation screen.
    mail_account = models.ForeignKey(
        "mail.MailAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        default=None,
        related_name="+",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "created_at"], name="bookingpage_owner"),
        ]

    def __str__(self):
        return self.title


class Booking(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    class CancelledBy(models.TextChoices):
        GUEST = "guest", "Guest"
        OWNER = "owner", "Owner"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    page = models.ForeignKey(
        BookingPage,
        on_delete=models.CASCADE,
        related_name="bookings",
    )
    start = models.DateTimeField()
    end = models.DateTimeField()
    guest_name = models.CharField(max_length=100)
    guest_email = models.EmailField()
    note = models.TextField(blank=True, default="")
    # The zone the guest browsed the page in: their mails give times in it.
    timezone = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.CONFIRMED,
    )
    cancelled_by = models.CharField(
        max_length=5, choices=CancelledBy.choices, blank=True, default=""
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, default=None)
    # The guest's only credential: it is in the link of their confirmation,
    # and lets them see and cancel this booking without an account.
    token = models.CharField(
        max_length=43, unique=True, default=_generate_booking_token
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        default=None,
        related_name="bookings",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start"]
        constraints = [
            # The last line against two guests taking the same slot at the
            # same instant; services.bookings serializes them first.
            models.UniqueConstraint(
                fields=["page", "start"],
                condition=models.Q(status="confirmed"),
                name="unique_confirmed_booking_slot",
            ),
        ]
        indexes = [
            models.Index(
                fields=["page", "status", "start"], name="booking_page_status"
            ),
        ]

    def __str__(self):
        return f"{self.guest_name} — {self.page} ({self.start})"

    @property
    def ical_uid(self):
        return booking_ical_uid(self.uuid)
