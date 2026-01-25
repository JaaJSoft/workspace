from django.urls import path

from . import views

app_name = 'files_ui'

urlpatterns = [
    path('', views.index, name='index'),
    path('/trash', views.trash, name='trash'),
    path('/view/<uuid:uuid>', views.view_file, name='view_file'),
    path('/<uuid:folder>', views.index, name='folder'),
]
