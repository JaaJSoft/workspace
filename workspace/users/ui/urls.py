from django.urls import path

from . import views

app_name = 'users_ui'

urlpatterns = [
    path('/<int:user_id>/card', views.user_card_view, name='user-card'),
    path('/profile', views.profile_view, name='profile'),
    path('/profile/<str:username>', views.profile_view, name='profile_by_username'),
    path('/settings', views.settings_view, name='settings'),
]
