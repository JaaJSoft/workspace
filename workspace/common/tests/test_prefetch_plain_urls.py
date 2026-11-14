from django.urls import path

from workspace.common.tests.test_prefetch import plain

urlpatterns = [path("plain", plain, name="plain")]
