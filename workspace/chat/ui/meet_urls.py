"""The one public UI route: /meet/<slug>.

Kept out of ``chat/ui/urls.py`` because everything there is mounted under
/chat and gated by ``login_required``; this page is neither.
"""

from django.urls import path

from . import views

app_name = "chat_meet"

urlpatterns = [
    path("/<str:slug>", views.meet_view, name="meet"),
]
