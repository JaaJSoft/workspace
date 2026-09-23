"""The Prometheus scrape endpoint."""

import os

from django.http import HttpResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    generate_latest,
)
from prometheus_client.multiprocess import MultiProcessCollector

from workspace.common.metrics import scrape_time_collectors


def metrics(request):
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        # This process's REGISTRY only holds its own share: sum every worker's
        # files instead. Process and GC metrics have no multiprocess form.
        registry = CollectorRegistry()
        MultiProcessCollector(registry)
        for collector in scrape_time_collectors():
            registry.register(collector)
    else:
        registry = REGISTRY
    return HttpResponse(generate_latest(registry), content_type=CONTENT_TYPE_LATEST)
