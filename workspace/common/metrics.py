"""Re-import-safe Prometheus metric constructors.

Module-level metric declarations like::

    counter = Counter('foo_total', 'help')

raise ``ValueError: Duplicated timeseries`` on a second import in the same
interpreter (test runners that purge ``sys.modules``, ``importlib.reload``,
some autoreload setups). The wrappers below catch that error and return the
already-registered instance, so the second import is a no-op.

Use these helpers instead of importing ``prometheus_client`` directly when the
metric lives at module scope and could be re-evaluated in the same process.
"""

import logging

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

logger = logging.getLogger(__name__)


def safe_counter(name, doc, labels=()):
    return _get_or_create(Counter, name, doc, labels)


def safe_gauge(name, doc, labels=()):
    return _get_or_create(Gauge, name, doc, labels)


def safe_histogram(name, doc, labels=(), **kwargs):
    return _get_or_create(Histogram, name, doc, labels, **kwargs)


def safe_register(collector):
    """Register a custom Collector idempotently with the default REGISTRY."""
    try:
        REGISTRY.register(collector)
    except ValueError:
        logger.debug(
            'Collector %s already registered; skipping',
            type(collector).__name__,
        )


def _get_or_create(cls, name, doc, labels, **kwargs):
    try:
        return cls(name, doc, labels, **kwargs)
    except ValueError as exc:
        # We only swallow the duplicated-timeseries case. Any other ValueError
        # (e.g. a real parameter mismatch like changed labels/doc) must surface
        # so it can be fixed at the call site instead of silently masked.
        if 'Duplicated timeseries' not in str(exc):
            raise
        existing = REGISTRY._names_to_collectors.get(name)
        if existing is None:
            raise
        logger.debug('Reusing existing metric %s on re-import', name)
        return existing
