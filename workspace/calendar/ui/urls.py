from django.urls import path

from . import views

app_name = 'calendar_ui'

urlpatterns = [
    path('', views.index, name='index'),
    path('/events/<uuid:event_id>/card', views.event_card, name='event-card'),
    path('/polls/shared/<str:token>', views.polls_shared, name='polls-shared'),
]
