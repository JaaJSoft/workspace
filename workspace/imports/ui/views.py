from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.common.uuids import parse_uuid_or_none

from ..providers.registry import provider_registry
from ..queries import user_connections_qs, user_jobs_qs
from ..serializers import ConnectionSerializer, JobSerializer


@login_required
@ensure_csrf_cookie
def index(request):
    connections = user_connections_qs(request.user)
    jobs = user_jobs_qs(request.user).select_related("connection")[:50]
    return render(
        request,
        "imports/ui/index.html",
        {
            "providers": [p.describe() for p in provider_registry.available()],
            "connections": ConnectionSerializer(connections, many=True).data,
            "jobs": JobSerializer(jobs, many=True).data,
            "open_wizard": request.GET.get("new") == "1",
            "highlight_job": str(parse_uuid_or_none(request.GET.get("job", "")) or ""),
        },
    )
