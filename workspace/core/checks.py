"""Deployment-time validation of how this instance runs its background tasks."""

from django.conf import settings
from django.core.checks import Error
from django.core.exceptions import ImproperlyConfigured


def in_memory_broker_error():
    """The error to report when no task sent from here can ever run, else None.

    The ``memory://`` broker lives inside one process: what the web server or
    the scheduler puts in it never reaches the worker, which polls a queue of
    its own. Only eager mode (``DEBUG``) makes it work, by running each task
    inline in the process that sends it.
    """
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return None
    if not settings.CELERY_BROKER_URL.startswith("memory://"):
        return None
    return Error(
        "Background tasks use the in-memory broker with DEBUG off: every task "
        "is dropped, since the web server, the worker and the scheduler each "
        "have a queue of their own.",
        hint="Set REDIS_URL to a Redis server all of them reach.",
        id="core.E001",
    )


def check_task_broker(app_configs, **kwargs):
    error = in_memory_broker_error()
    return [error] if error else []


def refuse_in_memory_broker():
    """Stop a process from starting when the tasks it sends would be dropped.

    Neither gunicorn nor a Celery worker or beat acts on the system checks, so
    each of them asks this before it starts serving.
    """
    error = in_memory_broker_error()
    if error:
        raise ImproperlyConfigured(f"{error.msg} {error.hint} ({error.id})")
