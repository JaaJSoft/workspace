"""Navigational prefetch, opt-in per view.

The browser may fetch a page while the pointer only rests on a link to it
(Speculation Rules, ``Sec-Purpose: prefetch``), so the click finds the page
already downloaded. That request runs the view for someone who may never open
the page. A view is therefore refused to a prefetch unless it declares, with
``@prefetchable``, that it has no effect a visit would have.

The declaration on the view is the only list of what may be prefetched. The
browser's rules are derived from it - the marked routes without a parameter,
reversed to the paths links point at - and ``PrefetchGateMiddleware`` refuses a
speculative request for every other view before it runs, whatever asked for it.
"""

import functools
import re

from django.conf import settings
from django.http import HttpResponse
from django.urls import URLResolver, get_resolver, reverse

# A link alpine-ajax intercepts swaps a fragment through fetch() and never
# navigates, so prefetching its page would be wasted work. A modifier missing
# here only costs that wasted prefetch.
AJAX_LINK_SELECTOR = r"[x-target], [x-target\.push], [x-target\.replace]"

_URL_PATTERN_SYNTAX = re.compile(r"([\\:*?+(){}\[\]])")

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


def prefetchable_routes(urlconf=None):
    """``(url name, takes parameters)`` of every named route served by a marked view."""
    return _walk(get_resolver(urlconf).url_patterns, "", False)


def _walk(patterns, namespace, takes_parameters):
    for entry in patterns:
        parameterized = takes_parameters or bool(entry.pattern.regex.groups)
        if isinstance(entry, URLResolver):
            inner = f"{namespace}{entry.namespace}:" if entry.namespace else namespace
            yield from _walk(entry.url_patterns, inner, parameterized)
        elif entry.name and is_prefetchable(entry.callback):
            yield f"{namespace}{entry.name}", parameterized


@functools.cache
def _prefetchable_paths(urlconf):
    # A route with a parameter stands for many paths, which the rules could only
    # name through a pattern: it is served to a prefetch, never asked for.
    return tuple(
        sorted(
            reverse(name, urlconf=urlconf)
            for name, takes_parameters in prefetchable_routes(urlconf)
            if not takes_parameters
        )
    )


def prefetchable_paths():
    return _prefetchable_paths(settings.ROOT_URLCONF)


def speculation_rules():
    """The Speculation Rules document of the site, or None when no view opted in."""
    paths = prefetchable_paths()
    if not paths:
        return None
    return {
        "prefetch": [
            {
                "source": "document",
                "where": {
                    "and": [
                        {
                            "href_matches": [
                                {"pathname": _URL_PATTERN_SYNTAX.sub(r"\\\1", path)}
                                for path in paths
                            ]
                        },
                        {"not": {"selector_matches": AJAX_LINK_SELECTOR}},
                    ]
                },
                "eagerness": "moderate",
            }
        ]
    }
