"""The public side of booking pages: anyone holding the link, no account."""

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.throttling import IpRateThrottle
from workspace.users.services.settings import get_user_language

from ..models import Booking
from ..queries import bookable_pages
from ..serializers_booking import BookRequestSerializer
from ..services.booking_slots import MAX_RANGE, free_slots
from ..services.bookings import PageUnavailable, SlotUnavailable, book, cancel


class BookingSlotsIpThrottle(IpRateThrottle):
    scope = "calendar.booking.slots.ip"


class BookingWriteIpThrottle(IpRateThrottle):
    scope = "calendar.booking.write.ip"


def owner_language(owner):
    """A booking page speaks its owner's language.

    An anonymous visitor has no language of their own (the browser's is
    ignored on purpose), and the people an owner sends their link to read the
    owner's.
    """
    return translation.override(get_user_language(owner))


def _active_page(token):
    return get_object_or_404(bookable_pages().select_related("owner"), token=token)


def _aware(value):
    moment = parse_datetime(value or "")
    if moment is None or moment.tzinfo is None:
        return None
    return moment


def booking_summary(booking):
    """A booking as its guest sees it: when, with whom, and nothing else."""
    page = booking.page
    return {
        "title": page.title,
        "owner_name": page.owner.get_full_name() or page.owner.username,
        "location": page.location,
        "start": booking.start.isoformat(),
        "end": booking.end.isoformat(),
        "guest_name": booking.guest_name,
        "status": booking.status,
    }


@extend_schema(tags=["Calendar - Booking pages (Public)"])
class SharedBookingSlotsView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [BookingSlotsIpThrottle]

    @extend_schema(
        summary="Free slots of a booking page",
        parameters=[
            OpenApiParameter("from", str, required=True, description="ISO instant"),
            OpenApiParameter("to", str, required=True, description="ISO instant"),
        ],
    )
    def get(self, request, token):
        page = _active_page(token)
        with owner_language(page.owner):
            return self._slots(request, page)

    def _slots(self, request, page):
        start = _aware(request.query_params.get("from"))
        end = _aware(request.query_params.get("to"))
        if start is None or end is None or end <= start:
            return Response(
                {"detail": _("Give a time range, from and to, with their offset.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if end - start > MAX_RANGE:
            return Response(
                {"detail": _("The range is too wide.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        slots = free_slots(page, start, end)
        return Response(
            {
                "duration_minutes": page.duration_minutes,
                "slots": [slot.isoformat() for slot in slots],
            }
        )


@extend_schema(tags=["Calendar - Booking pages (Public)"])
class SharedBookingBookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [BookingWriteIpThrottle]

    @extend_schema(summary="Book a slot", request=BookRequestSerializer)
    def post(self, request, token):
        page = _active_page(token)
        with owner_language(page.owner):
            return self._book(request, page)

    def _book(self, request, page):
        ser = BookRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        try:
            booking = book(
                page,
                start=d["start"],
                guest_name=d["name"].strip(),
                guest_email=d["email"],
                note=d["note"].strip(),
                guest_timezone=d["timezone"],
                base_url=request.build_absolute_uri("/"),
            )
        except PageUnavailable:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except SlotUnavailable:
            return Response(
                {"detail": _("This time is no longer available. Pick another one.")},
                status=status.HTTP_409_CONFLICT,
            )
        data = booking_summary(booking)
        # Handed to the guest who just booked, and to nobody else: it is the
        # only credential behind their cancellation link.
        data["manage_url"] = request.build_absolute_uri(
            reverse("calendar_ui:booking-manage", args=[booking.token])
        )
        data["mail_sent"] = page.mail_account_id is not None
        return Response(data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Calendar - Booking pages (Public)"])
class SharedBookingCancelView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [BookingWriteIpThrottle]

    @extend_schema(summary="Cancel a booking from its guest link", request=None)
    def post(self, request, token):
        booking = get_object_or_404(
            Booking.objects.select_related("page__owner"), token=token
        )
        with owner_language(booking.page.owner):
            return self._cancel(request, booking)

    def _cancel(self, request, booking):
        if booking.status == Booking.Status.CONFIRMED:
            if booking.start <= timezone.now():
                return Response(
                    {"detail": _("This appointment has already started.")},
                    status=status.HTTP_409_CONFLICT,
                )
            cancel(booking, by=Booking.CancelledBy.GUEST)
            booking.refresh_from_db()
        return Response(booking_summary(booking))
