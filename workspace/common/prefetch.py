"""Navigational prefetch, opt-in per view.

The browser may fetch a page while the pointer only rests on a link to it
(Speculation Rules, ``Sec-Purpose: prefetch``), so the click finds the page
already downloaded. That request runs the view for someone who may never open
the page. A view is therefore refused to a prefetch unless it declares, with
``@prefetchable``, that it has no effect a visit would have.

The browser's rules stay broad - any same-origin link - and the declaration on
the view is the only list of what may be prefetched: ``PrefetchGateMiddleware``
refuses a speculative request for every other view before it runs.
"""

from django.http import HttpResponse

PREFETCHABLE_ATTRIBUTE = "prefetchable"


def prefetchable(view):
    """Let the browser fetch this view ahead of a click.

    The view runs while the user is only hovering a link and may never open
    the page: it must leave nothing behind a visit would - no mark-as-read, no
    "last opened" bookmark, no activity entry, no task. Idempotent lazy setup
    (a home folder created on the first visit) is fine.

    Applies to a function view or a class-based view, in either order with
    other decorators (``functools.wraps`` carries the attribute up).
    """
    setattr(view, PREFETCHABLE_ATTRIBUTE, True)
    return view


def is_prefetchable(view):
    if getattr(view, PREFETCHABLE_ATTRIBUTE, False):
        return True
    return bool(
        getattr(getattr(view, "view_class", None), PREFETCHABLE_ATTRIBUTE, False)
    )


def speculative_purpose(request):
    """The parameters of a speculative request (``{"prerender"}``...), or None.

    ``Sec-Purpose`` is a structured list item: ``prefetch`` followed by its
    parameters. ``Purpose: prefetch`` is the older spelling of the same thing.
    """
    header = request.headers.get("Sec-Purpose") or request.headers.get("Purpose") or ""
    token, *parameters = (part.strip() for part in header.split(";"))
    if token != "prefetch":
        return None
    return {parameter.split("=", 1)[0] for parameter in parameters}


def is_speculative(request):
    return speculative_purpose(request) is not None


def refused_speculation():
    # Any non-2xx answer makes the browser drop the prefetch and load the page
    # normally on click. Never a 204: a prefetched 204 used as the navigation
    # would leave the user on the page they clicked from.
    return HttpResponse(status=403, headers={"Cache-Control": "no-store"})
