"""The public UI routes: /meet/<slug> and the message list it loads.

Kept out of ``chat/ui/urls.py`` because everything there is mounted under
/chat and gated by ``login_required``; these are neither - the guest is
authorized by the meeting token they send, not by a session.
"""

from django.urls import path

from . import views

app_name = "chat_meet"

urlpatterns = [
    path("/<str:slug>", views.meet_view, name="meet"),
    path("/<str:slug>/messages", views.meet_messages_view, name="messages"),
]
