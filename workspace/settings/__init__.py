"""Django settings for the Workspace project.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/5.2/ref/settings/

The configuration is split by topic across the modules below. Django reads
settings as attributes of this package, so each module is star-imported here;
this is the one place in the codebase where that is the intended mechanism
rather than a re-export. A setting is defined in exactly one module - add new
ones next to the topic they configure, never here.

Import order below is irrelevant (and therefore left alphabetical): a module
that needs a value from another one imports it explicitly, so nothing depends
on this file's sequence.
"""

from .ai import *  # noqa: F403
from .api import *  # noqa: F403
from .apps import *  # noqa: F403
from .base import *  # noqa: F403
from .cache import *  # noqa: F403
from .celery import *  # noqa: F403
from .chat import *  # noqa: F403
from .db import *  # noqa: F403
from .debug_toolbar import *  # noqa: F403
from .files import *  # noqa: F403
from .mail import *  # noqa: F403
from .middleware import *  # noqa: F403
from .monitoring import *  # noqa: F403
from .notifications import *  # noqa: F403
from .oidc import *  # noqa: F403
from .security import *  # noqa: F403
from .storage import *  # noqa: F403
from .templates import *  # noqa: F403
