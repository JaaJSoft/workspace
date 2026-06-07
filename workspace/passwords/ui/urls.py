from django.urls import path
from . import views

app_name = 'passwords_ui'

urlpatterns = [
    path('', views.index, name='index'),
    path('/<uuid:uuid>', views.vault_detail, name='vault_detail'),
]
