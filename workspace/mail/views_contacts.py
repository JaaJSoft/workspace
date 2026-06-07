import logging
from collections import Counter, defaultdict

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.common.uuids import parse_uuid_or_none

from .models import MailMessage
from .queries import user_account_ids

logger = logging.getLogger(__name__)


@extend_schema(tags=["Mail"])
class ContactAutocompleteView(CacheControlMixin, APIView):
    cache_max_age = 300
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Autocomplete contacts from message history",
        parameters=[
            OpenApiParameter(
                "q", str, required=True, description="Search query (min 2 chars)"
            ),
            OpenApiParameter(
                "account_id", str, required=False, description="Filter by account"
            ),
        ],
    )
    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return Response([])

        account_filter = Q(account_id__in=user_account_ids(request.user))
        account_id = request.query_params.get("account_id")
        if account_id:
            # Reject malformed UUIDs at the boundary: passing a non-UUID
            # string straight to Q(account__uuid=...) crashes deep in Django's
            # UUIDField cleaning layer and surfaces as 500.
            account_uuid = parse_uuid_or_none(account_id)
            if account_uuid is None:
                return Response(
                    {"detail": '"account_id" must be a valid UUID.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            account_filter &= Q(account__uuid=account_uuid)

        q_lower = q.lower()

        rows = (
            MailMessage.objects.filter(account_filter, deleted_at__isnull=True)
            .filter(
                Q(from_address__icontains=q)
                | Q(to_addresses__icontains=q)
                | Q(cc_addresses__icontains=q)
            )
            .order_by("-date")
            .values("from_address", "to_addresses", "cc_addresses")[:500]
        )

        # Extract all addresses and count frequency
        email_count = Counter()
        email_names = defaultdict(Counter)

        for row in rows:
            addresses = []
            fa = row["from_address"]
            if isinstance(fa, dict) and fa.get("email"):
                addresses.append(fa)
            for field in (row["to_addresses"], row["cc_addresses"]):
                if isinstance(field, list):
                    addresses.extend(
                        a for a in field if isinstance(a, dict) and a.get("email")
                    )

            for addr in addresses:
                email = addr["email"].strip().lower()
                name = (addr.get("name") or "").strip()
                # Post-filter: check that the query actually matches this contact
                if q_lower not in email and q_lower not in name.lower():
                    continue
                email_count[email] += 1
                if name:
                    email_names[email][name] += 1

        # Build results sorted by frequency
        results = []
        for email, count in email_count.most_common(15):
            name_counter = email_names.get(email)
            name = name_counter.most_common(1)[0][0] if name_counter else ""
            results.append({"name": name, "email": email, "count": count})

        return Response(results)
