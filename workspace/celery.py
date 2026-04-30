"""Celery application for the Workspace project."""

import logging
import os
import time

from celery import Celery
from celery.signals import task_failure, task_postrun, task_prerun, task_retry
from prometheus_client import Counter, Histogram
from prometheus_client.core import REGISTRY, GaugeMetricFamily

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'workspace.settings')

logger = logging.getLogger(__name__)

app = Celery('workspace')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


# ---------------------------------------------------------------------------
# Prometheus metrics
#
# All metric names in this file MUST start with "celery_".
# ---------------------------------------------------------------------------
_P = 'celery'

CELERY_TASK_DURATION = Histogram(
    f'{_P}_task_duration_seconds',
    'Time between task_prerun and task_postrun for one execution attempt',
    ['task'],
)

CELERY_TASKS_TOTAL = Counter(
    f'{_P}_tasks_total',
    'Task executions completed, by task name and final state (success/failure/retry)',
    ['task', 'state'],
)

# Per-task wall-clock start time, keyed by task_id. Cleared in task_postrun.
_task_starts: dict[str, float] = {}


@task_prerun.connect
def _on_task_prerun(task_id=None, task=None, **kwargs):
    if task_id is not None:
        _task_starts[task_id] = time.monotonic()


@task_postrun.connect
def _on_task_postrun(task_id=None, task=None, state=None, **kwargs):
    started = _task_starts.pop(task_id, None) if task_id else None
    name = getattr(task, 'name', 'unknown')
    if started is not None:
        CELERY_TASK_DURATION.labels(task=name).observe(time.monotonic() - started)
    # state is one of 'SUCCESS', 'FAILURE', 'RETRY' (uppercase from celery.states).
    label = (state or 'unknown').lower()
    CELERY_TASKS_TOTAL.labels(task=name, state=label).inc()


@task_failure.connect
def _on_task_failure(sender=None, **kwargs):
    # task_postrun also fires with state='FAILURE', so we don't double-count here.
    # This handler exists so that catastrophic failures (worker crash mid-task)
    # which skip task_postrun still leave a trace.
    pass


@task_retry.connect
def _on_task_retry(sender=None, **kwargs):
    # Same rationale as above: task_postrun handles the increment in-band.
    pass


# ---------------------------------------------------------------------------
# Custom Collector — queue length sampled at scrape time via Redis LLEN.
# ---------------------------------------------------------------------------
class _CeleryQueueLengthCollector:
    """Exposes celery_queue_length{queue} by calling LLEN on each known queue.

    Scrape-time collection is fine here because LLEN is O(1) on Redis lists and
    a Prometheus scrape happens at most every ~15s. If the broker is not Redis
    (e.g., 'memory://' in dev/tests), this collector emits no samples.
    """

    def collect(self):
        gauge = GaugeMetricFamily(
            f'{_P}_queue_length',
            'Number of pending messages in a Celery broker queue',
            labels=['queue'],
        )
        try:
            from django.conf import settings
            broker = getattr(settings, 'CELERY_BROKER_URL', '') or ''
            if not broker.startswith('redis://') and not broker.startswith('rediss://'):
                return  # skip non-Redis brokers (memory://, amqp://, ...)

            queues = getattr(settings, 'CELERY_TASK_QUEUES', None) or []
            queue_names = [q.name for q in queues] or ['celery']

            import redis
            client = redis.Redis.from_url(broker)
            try:
                for name in queue_names:
                    try:
                        length = client.llen(name)
                    except Exception:
                        logger.exception("LLEN failed for celery queue '%s'", name)
                        continue
                    gauge.add_metric([name], length)
            finally:
                client.close()
        except Exception:
            logger.exception('celery_queue_length collector failed')
            return
        yield gauge


# Guarded against double-registration: in test harnesses or autoreload setups
# the module can be re-imported in the same interpreter, which would otherwise
# raise ValueError on duplicate metric names.
try:
    REGISTRY.register(_CeleryQueueLengthCollector())
except ValueError:
    logger.debug('Celery queue length collector already registered')
