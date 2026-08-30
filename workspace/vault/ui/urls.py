from django.urls import path

from . import views

app_name = "vault_ui"

urlpatterns = [
    path("", views.index, name="index"),
    # One view, two URLs, as in chat and files: a palette command is a
    # plain link and can name no UUID, so `?action=new` has to land on a
    # page that can also be reached with a vault already chosen.
    path("/<uuid:vault_uuid>", views.index, name="vault"),
    path("/onboarding", views.onboarding, name="onboarding"),
]
