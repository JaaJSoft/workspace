from django.urls import path

from . import views

app_name = "vault_ui"

urlpatterns = [
    path("", views.index, name="index"),
    path("/onboarding", views.onboarding, name="onboarding"),
]
