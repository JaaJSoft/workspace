"""The meeting page: one route for hosts and guests alike.

Kept out of chat/ui/urls.py, whose routes are mounted under /chat and gated
by login_required; this one decides per visitor.
"""

from django.urls import path

from . import views

app_name = "chat_meeting"

urlpatterns = [
    path("/<str:slug>", views.meeting_view, name="page"),
]
