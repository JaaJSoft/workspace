"""Celery priority for bulk background work."""

# The lowest Redis priority. Kombu keeps each priority step in a list of its
# own and the worker drains the lower numbers first; a task sent without a
# priority gets 0. A backlog sent at this priority therefore runs after every
# task already waiting. It does not preempt: a worker process may already have
# reserved one of these (CELERY_WORKER_PREFETCH_MULTIPLIER), which runs before
# a task that arrives meanwhile.
BACKGROUND_PRIORITY = 9
