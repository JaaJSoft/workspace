from django.urls import path

from . import views

app_name = 'chat_ui'

urlpatterns = [
    path('', views.chat_view, name='index'),
    path('/conversations', views.conversation_list_view, name='conversation_list'),
]
