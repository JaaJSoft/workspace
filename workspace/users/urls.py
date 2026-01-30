from django.urls import path

from . import views

urlpatterns = [
    # API endpoints
    path('api/v1/users/me', views.UserMeView.as_view(), name='user-me'),
    path('api/v1/users/me/password', views.ChangePasswordView.as_view(), name='user-change-password'),
    path('api/v1/users/password-rules', views.PasswordRulesView.as_view(), name='user-password-rules'),
    # UI
    path('profile', views.profile_view, name='user_profile'),
]
