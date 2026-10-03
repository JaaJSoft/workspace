from django.urls import path

from workspace.mail.views.oauth2 import oauth2_callback

from . import views

app_name = "mail_ui"

urlpatterns = [
    path("", views.index, name="index"),
    path("/contact-card", views.contact_card, name="contact-card"),
    path("/oauth2/callback", oauth2_callback, name="mail-oauth2-callback"),
]
