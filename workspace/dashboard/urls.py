from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("dashboard/activity", views.activity_feed, name="activity_feed"),
    path("dashboard/today", views.today_fragment, name="today_fragment"),
    path("dashboard/modules", views.modules_fragment, name="modules_fragment"),
]
