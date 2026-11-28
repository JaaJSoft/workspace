from django.http import HttpResponse
from django.utils.translation import gettext as _
from django_filters.utils import translate_validation
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from workspace.common.exceptions import Conflict
from workspace.common.filters import KeyFilter, without_none
from workspace.common.http_ranges import safe_filename
from workspace.common.mixins import CacheControlMixin
from workspace.common.qr import QRTooLarge, qr_response

from ..filters import PersonFilter
from ..queries import user_person_lists
from ..serializers import (
    ImportReportSerializer,
    PersonImportSerializer,
    QRCodeQuerySerializer,
    book_of,
)
from ..services.qr import person_qr_code
from ..services.vcard import VCardError
from ..services.vcard_export import export_vcards
from ..services.vcard_import import import_vcards
from .lists import PersonListRowsMixin
from .persons import PersonRowsMixin

VCARD_CONTENT_TYPE = "text/vcard; charset=utf-8"
_VCF = {(200, "text/vcard"): OpenApiTypes.BINARY}


def _vcf_response(text, filename):
    response = HttpResponse(text, content_type=VCARD_CONTENT_TYPE)
    response["Content-Disposition"] = (
        f'attachment; filename="{safe_filename(filename)}.vcf"'
    )
    return response


def _decode(data):
    """A vCard is UTF-8 by spec; a 2.1 export from an old phone may not be."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


@extend_schema(tags=["People"])
class PersonImportView(GenericAPIView):
    parser_classes = [MultiPartParser]
    serializer_class = PersonImportSerializer

    @extend_schema(
        summary="Import a vCard file",
        description=(
            "Into the address book of `group`, or the caller's own without "
            "one. A card of a contact already there brings it up to date."
        ),
        request={"multipart/form-data": PersonImportSerializer},
        responses=ImportReportSerializer,
    )
    def post(self, request, *args, **kwargs):
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        book = book_of(None, body.validated_data, request.user)
        try:
            report = import_vcards(_decode(body.validated_data["file"].read()), **book)
        except VCardError:
            raise ValidationError({"file": [_("This is not a vCard file.")]}) from None
        return Response(ImportReportSerializer(report).data)


@extend_schema(tags=["People"])
class PersonExportView(PersonRowsMixin, GenericAPIView):
    """The contacts the list of contacts would answer for the same filters,
    as one file, with the contact lists of the address books asked for."""

    filterset_class = PersonFilter
    # A file holds what was asked for whole.
    pagination_class = None

    def _lists(self, asked):
        """The contact lists that go with the contacts *asked* for: the ones
        of their address books, or the ones named."""
        values = asked.form.cleaned_data
        lists = user_person_lists(self.request.user)
        for name in ("group", "group_name"):
            lists = asked.filters[name].filter(lists, values[name])
        if values["list"]:
            lists = lists.filter(uuid__in=without_none(values["list"])[0])
        if values["list_name"]:
            lists = lists.filter(
                KeyFilter(field_name="name").condition(values["list_name"])
            )
        return lists.prefetch_related("members")

    def _filename(self, asked):
        groups, own_book = without_none(asked.form.cleaned_data["group"] or [])
        if own_book and not groups:
            return "my-contacts"
        if len(groups) == 1 and not own_book:
            group = self.request.user.groups.filter(pk=groups[0]).first()
            if group is not None:
                return group.name
        return "contacts"

    @extend_schema(summary="Export contacts as a vCard file", responses=_VCF)
    def get(self, request, *args, **kwargs):
        asked = PersonFilter(
            request.query_params, queryset=self.get_queryset(), request=request
        )
        if not asked.is_valid():
            raise translate_validation(asked.errors)
        text = export_vcards(asked.qs, self._lists(asked))
        return _vcf_response(text, self._filename(asked))


@extend_schema(tags=["People"])
class PersonDownloadView(PersonRowsMixin, GenericAPIView):
    lookup_field = "uuid"

    @extend_schema(summary="Download a contact as a vCard file", responses=_VCF)
    def get(self, request, *args, **kwargs):
        person = self.get_object()
        return _vcf_response(export_vcards([person]), person.display_name)


@extend_schema(tags=["People"])
class PersonListDownloadView(PersonListRowsMixin, GenericAPIView):
    @extend_schema(
        summary="Download a contact list and its members as a vCard file",
        responses=_VCF,
    )
    def get(self, request, *args, **kwargs):
        person_list = self.get_object()
        text = export_vcards(person_list.members.all(), [person_list])
        return _vcf_response(text, person_list.name)


@extend_schema(tags=["People"])
class PersonQRCodeView(CacheControlMixin, PersonRowsMixin, GenericAPIView):
    """One contact as a QR code: pointing another phone's camera at it files
    the card in its address book, no file and no account in between."""

    lookup_field = "uuid"
    # A contact's card is personal data: `private` alone still lets the
    # browser keep it on disk, where the next account on a shared machine
    # finds it. The image is cheap to rebuild, so nothing is stored.
    cache_no_store = True

    @extend_schema(
        summary="Render a contact as a QR code",
        description=(
            "Answers 409 for a contact that holds more than a QR code can "
            "carry: its vCard file has no such limit."
        ),
        parameters=[QRCodeQuerySerializer],
        responses={
            (200, "image/svg+xml"): OpenApiTypes.BINARY,
            (200, "image/png"): OpenApiTypes.BINARY,
        },
    )
    def get(self, request, *args, **kwargs):
        asked = QRCodeQuerySerializer(data=request.query_params)
        asked.is_valid(raise_exception=True)
        person = self.get_object()
        try:
            code = person_qr_code(person)
        except QRTooLarge:
            raise Conflict(
                _(
                    "This contact holds more than a QR code can carry. "
                    "Export it as a .vcf file instead."
                )
            ) from None
        return qr_response(code, asked.validated_data["kind"], person.display_name)
