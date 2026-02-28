from django.urls import path

from . import views
from workspace.mail.views_oauth2 import oauth2_callback

app_name = 'mail_ui'

urlpatterns = [
    path('', views.index, name='index'),
    path('/oauth2/callback', oauth2_callback, name='mail-oauth2-callback'),
]
