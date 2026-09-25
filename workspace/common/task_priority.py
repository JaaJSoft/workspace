"""Celery priority for bulk background work."""

# The lowest Redis priority. Kombu keeps each priority step in a list of its
# own and the worker drains the lower numbers first; a task sent without a
# priority gets 0. A backlog of thousands of tasks sent at this priority
# therefore never holds up the ones a user is waiting on.
BACKGROUND_PRIORITY = 9
