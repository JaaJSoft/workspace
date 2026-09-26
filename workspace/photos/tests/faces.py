"""Shared setup for the face tests: the fake backend, the feature turned on."""

from django.core.cache import cache
from django.test import override_settings

from workspace.photos.services.face_preferences import FACES_ENABLED, MODULE
from workspace.users.services.settings import set_setting

faces_on = override_settings(
    PHOTOS_FACES_ENABLED=True,
    PHOTOS_FACE_BACKEND="fake",
    PHOTOS_FACES_MAX_DISTANCE=None,
)


def opt_in(user):
    set_setting(user, MODULE, FACES_ENABLED, True)


class FacesTestMixin:
    """Mixed in before TestCase: the settings cache is process-global."""

    def tearDown(self):
        cache.clear()
        super().tearDown()
