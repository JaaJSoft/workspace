"""Celery priority for bulk background work."""

# The lowest Redis priority. Kombu keeps each priority step in a list of its
# own and the worker drains the lower numbers first; a task sent without a
# priority gets 0. A backlog sent at this priority therefore runs after every
# task already waiting. It does not preempt: a worker that reserved a few of
# these ahead (worker_prefetch_multiplier, 4 per process by default) runs
# them before a task that arrives meanwhile.
BACKGROUND_PRIORITY = 9
