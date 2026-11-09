"""The exception handler every API view answers through."""

import errno

from rest_framework import status
from rest_framework.response import Response

from .module_guard import exception_handler as module_guard_exception_handler


def exception_handler(exc, context):
    # A name the storage cannot hold - a key past the 1024 bytes S3 allows, a
    # path component past the filesystem's 255 - is the request's to change.
    if isinstance(exc, OSError) and exc.errno == errno.ENAMETOOLONG:
        return Response(
            {"detail": "This name is too long to be stored."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return module_guard_exception_handler(exc, context)
