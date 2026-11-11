from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def offline_page(request):
    """The page the service worker shows when a navigation finds no network.

    The worker caches it when it installs, so it is written in the language
    active at that moment. The language is part of the worker's URL: changing
    it installs a new worker, which caches the page again.
    """
    return render(request, "core/offline.html")
