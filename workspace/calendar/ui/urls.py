from django.urls import path

from . import views

app_name = 'calendar_ui'

urlpatterns = [
    path('', views.index, name='index'),
    path('/polls/shared/<str:token>', views.polls_shared, name='polls-shared'),
]
