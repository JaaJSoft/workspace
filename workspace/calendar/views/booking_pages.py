"""Booking pages and their bookings, as their owner manages them."""

from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import parse_uuid_or_none
from workspace.users.services.settings import get_user_timezone

from ..models import Booking, BookingPage
from ..serializers_booking import BookingPageSerializer, BookingSerializer
from ..services.bookings import cancel

# The owner's list shows what is coming; a page booked solid for the year is
# still read a screen at a time.
MAX_BOOKINGS = 200


def _owned_pages(user):
    now = timezone.now()
    return (
        BookingPage.objects.filter(owner=user)
        .prefetch_related("busy_calendars")
        .annotate(
            upcoming_count=Count(
                "bookings",
                filter=Q(
                    bookings__status=Booking.Status.CONFIRMED,
                    bookings__end__gt=now,
                ),
            )
        )
    )


@extend_schema(tags=["Calendar - Booking pages"])
class BookingPageListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List my booking pages", responses=BookingPageSerializer(many=True)
    )
    def get(self, request):
        pages = _owned_pages(request.user)
        return Response(
            BookingPageSerializer(pages, many=True, context={"request": request}).data
        )

    @extend_schema(
        summary="Create a booking page",
        request=BookingPageSerializer,
        responses=BookingPageSerializer,
    )
    @transaction.atomic
    def post(self, request):
        ser = BookingPageSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        if "timezone" not in ser.validated_data:
            page = ser.save(
                owner=request.user, timezone=get_user_timezone(request.user).key
            )
        else:
            page = ser.save(owner=request.user)
        page = _owned_pages(request.user).get(pk=page.pk)
        return Response(
            BookingPageSerializer(page, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Calendar - Booking pages"])
class BookingPageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_page(self, request, page_id):
        return get_object_or_404(_owned_pages(request.user), uuid=page_id)

    @extend_schema(summary="Get a booking page", responses=BookingPageSerializer)
    def get(self, request, page_id):
        page = self._get_page(request, page_id)
        return Response(BookingPageSerializer(page, context={"request": request}).data)

    @extend_schema(
        summary="Update a booking page",
        request=BookingPageSerializer,
        responses=BookingPageSerializer,
    )
    @transaction.atomic
    def patch(self, request, page_id):
        page = self._get_page(request, page_id)
        ser = BookingPageSerializer(
            page, data=request.data, partial=True, context={"request": request}
        )
        ser.is_valid(raise_exception=True)
        ser.save()
        page = self._get_page(request, page_id)
        return Response(BookingPageSerializer(page, context={"request": request}).data)

    @extend_schema(
        summary="Delete a booking page",
        description=(
            "Appointments already booked stay on the calendar; their guests "
            "can no longer cancel them online."
        ),
    )
    def delete(self, request, page_id):
        page = self._get_page(request, page_id)
        page.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Calendar - Booking pages"])
class BookingListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List the upcoming bookings of my pages",
        parameters=[
            OpenApiParameter("page", str, description="Only this booking page"),
        ],
        responses=BookingSerializer(many=True),
    )
    def get(self, request):
        bookings = Booking.objects.filter(
            page__owner=request.user,
            status=Booking.Status.CONFIRMED,
            end__gt=timezone.now(),
        ).select_related("page")
        page_param = request.query_params.get("page")
        if page_param:
            page_id = parse_uuid_or_none(page_param)
            if page_id is None:
                return Response(
                    {"page": [_("Not a valid UUID.")]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            bookings = bookings.filter(page_id=page_id)
        bookings = bookings.order_by("start")[:MAX_BOOKINGS]
        return Response(BookingSerializer(bookings, many=True).data)


@extend_schema(tags=["Calendar - Booking pages"])
class BookingCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Cancel a booking of one of my pages",
        description="Removes the event and mails the guest when the page has a mail account.",
        request=None,
        responses=BookingSerializer,
    )
    def post(self, request, booking_id):
        booking = get_object_or_404(
            Booking.objects.select_related("page"),
            uuid=booking_id,
            page__owner=request.user,
        )
        if not cancel(booking, by=Booking.CancelledBy.OWNER):
            return Response(
                {"detail": _("This booking is already cancelled.")},
                status=status.HTTP_409_CONFLICT,
            )
        booking.refresh_from_db()
        return Response(BookingSerializer(booking).data)
