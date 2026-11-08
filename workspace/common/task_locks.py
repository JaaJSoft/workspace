"""Advisory locks for periodic Celery tasks, backed by the shared cache.

Beat schedules fire on a fixed period, not on "the previous run finished".
When a job's runtime creeps past its period - a tree that grew, a slow
disk, a backed-up broker - beat keeps enqueueing, and every queued copy
redoes the same work. Load then compounds instead of plateauing, which is
the usual mechanism behind "the cron suddenly takes forever".

An advisory lock breaks that loop: the copy that loses the race exits
immediately instead of duplicating the work.

Correctness rests on ``cache.add`` being atomic - Redis ``SET NX`` in
production, an internally locked dict for ``LocMemCache`` in tests - so
two concurrent workers cannot both observe the key as free.

This is deliberately *advisory*: the ``timeout`` is a safety valve so a
worker killed mid-run (OOM, SIGKILL, node loss) cannot wedge the job
forever, and it means the lock can expire under a run that outlives it.
Use it to collapse redundant work, not to guarantee mutual exclusion over
something that would corrupt data if it ran twice - use a database
constraint or :mod:`workspace.common.celery_claim` for that.

Each holder stores a token of its own as the value and releases the key only
while it still holds that token. Once a run has outlived the TTL the key may
belong to the next holder; deleting it unconditionally would let a third run
in alongside the second.
"""

import logging
import secrets
from contextlib import contextmanager

from django.core.cache import cache

logger = logging.getLogger(__name__)

_RELEASE_IF_OWNED = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


def _redis_client(backend):
    """The django-redis client of *backend* when it stores values verbatim.

    The herd client wraps every value and the sharded one routes keys to
    several servers, so neither can compare a stored value in one call.
    """
    try:
        from django_redis.client import DefaultClient, HerdClient, ShardClient
    except ImportError:
        return None
    client = getattr(backend, "client", None)
    if isinstance(client, DefaultClient) and not isinstance(
        client, HerdClient | ShardClient
    ):
        return client
    return None


def _release(key, token):
    client = _redis_client(cache)
    if client is not None:
        client.get_client(write=True).eval(
            _RELEASE_IF_OWNED, 1, client.make_key(key), client.encode(token)
        )
        return
    # No atomic compare-and-delete here: another holder can still take the key
    # between the read and the delete, so this narrows the race, not closes it.
    if cache.get(key) == token:
        cache.delete(key)


@contextmanager
def task_lock(key, timeout):
    """Hold an advisory lock named *key* for the body of the ``with`` block.

    Yields ``True`` when the lock was acquired and ``False`` when another
    holder owns it - callers are expected to branch on the yielded value
    and return early, so the "already running" path stays explicit at the
    call site rather than hidden in an exception.

    ``timeout`` is the lock's TTL in seconds; set it above the job's
    expected runtime so a healthy run never releases the lock early.
    The lock is released on the way out, including on exception, so a
    failed run does not block the next scheduled attempt - unless it
    expired and another holder took it meanwhile, whose lock stays.
    """
    token = secrets.token_hex(16)
    acquired = cache.add(key, token, timeout)
    try:
        yield acquired
    finally:
        if acquired:
            # Best-effort: a cache blip here only means the lock lingers
            # until its TTL, which is the safe direction to fail.
            try:
                _release(key, token)
            except Exception:
                logger.exception("Failed to release task lock %s", key)
