from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from ..queries import user_connections_qs, user_jobs_qs


@login_required
@ensure_csrf_cookie
def index(request):
    return render(
        request,
        "imports/ui/index.html",
        {
            "connections": user_connections_qs(request.user),
            "jobs": user_jobs_qs(request.user).select_related("connection")[:50],
        },
    )
