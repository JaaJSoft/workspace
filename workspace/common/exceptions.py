"""API errors DRF has no class for."""

from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException


class Conflict(APIException):
    """The request is valid and the current state refuses it: a name already
    taken, a sprint already running. Fixing the request changes nothing; the
    state has to change first."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = _("The current state does not allow this.")
    default_code = "conflict"
