from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


@csrf_exempt
@require_http_methods(["GET", "POST"])
def incoming(request):
    """Where a share from another app lands when no service worker took it.

    The manifest's share_target posts to this URL, and the service worker
    answers that post itself: it keeps what was shared in the browser and
    sends the app to the page of the module that receives it (sw.js,
    receiveIncoming). A share reaches the server only when no worker ran -
    site data cleared, a worker older than the manifest - and what it carried
    is lost by then: the body is never read, so nothing is stored and the
    missing CSRF token protects nothing. The Files page tells the user to
    share again.
    """
    return redirect(reverse("files_ui:index") + "?incoming=")
