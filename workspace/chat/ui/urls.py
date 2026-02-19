from django.urls import path

from . import views

app_name = 'chat_ui'

urlpatterns = [
    path('', views.chat_view, name='index'),
    path('/<uuid:conversation_uuid>', views.chat_view, name='conversation'),
    path('/conversations', views.conversation_list_view, name='conversation_list'),
    path('/<uuid:conversation_uuid>/messages', views.conversation_messages_view, name='conversation_messages'),
    path('/<uuid:conversation_uuid>/messages/<uuid:message_uuid>/readers', views.message_readers_view, name='message_readers'),
]
