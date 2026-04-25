from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('dashboard/activity', views.activity_feed, name='activity_feed'),
    path('dashboard/upcoming', views.upcoming_fragment, name='upcoming_fragment'),
]
