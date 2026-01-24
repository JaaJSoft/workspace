from django.urls import path

from . import views

app_name = 'files_ui'

urlpatterns = [
    path('', views.index, name='index'),
    path('/<uuid:folder>', views.index, name='folder'),
]
