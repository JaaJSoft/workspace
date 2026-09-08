"""The first iteration's public link, redirected.

Kept out of ``chat/ui/urls.py`` because everything there is mounted under
/chat and gated by ``login_required``; this one is neither - it answers
anyone, and sends them to /meetings/<slug>.
"""

from django.urls import path

from . import views

app_name = "chat_meet"

urlpatterns = [
    path("/<str:slug>", views.meet_redirect_view, name="meet"),
]
