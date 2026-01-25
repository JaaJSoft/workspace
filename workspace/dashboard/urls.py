from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('dashboard/stats', views.stats, name='stats'),
    path('dashboard/insights/recent', views.insights_recent, name='insights_recent'),
    path('dashboard/insights/favorites', views.insights_favorites, name='insights_favorites'),
    path('dashboard/insights/trash', views.insights_trash, name='insights_trash'),
]
