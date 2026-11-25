from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from workspace.common.uuids import parse_uuid_or_none

from ..importers.base import importer_registry
from ..importers.files import ON_CONFLICT_LABELS
from ..models import ImportJob
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
            "groups": list(request.user.groups.order_by("name").values("id", "name")),
            "kinds": [
                {
                    "kind": kind,
                    "name": str(importer_registry.get(kind).label),
                    "description": str(importer_registry.get(kind).description),
                }
                for kind in importer_registry.kinds()
            ],
            "job_statuses": [
                {
                    "value": value,
                    "label": str(label),
                    "active": value not in ImportJob.TERMINAL_STATUSES,
                }
                for value, label in ImportJob.Status.choices
            ],
            "conflict_policies": [
                {"value": value, "label": str(label), "summary": str(summary)}
                for value, (label, summary) in ON_CONFLICT_LABELS.items()
            ],
        },
    )
