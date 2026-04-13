from django.urls import path

from . import views

urlpatterns = [
    # Auth / API tokens
    path('api/v1/auth/tokens', views.APITokenListCreateView.as_view(), name='api-token-list-create'),
    path('api/v1/auth/tokens/<int:pk>', views.APITokenDetailView.as_view(), name='api-token-detail'),
    # API endpoints
    path('api/v1/users/search', views.UserSearchView.as_view(), name='user-search'),
    path('api/v1/users/me', views.UserMeView.as_view(), name='user-me'),
    path('api/v1/users/me/password', views.ChangePasswordView.as_view(), name='user-change-password'),
    path('api/v1/users/me/avatar', views.UserAvatarUploadView.as_view(), name='user-avatar-upload'),
    path('api/v1/users/me/status', views.UserStatusView.as_view(), name='user-status'),
    path('api/v1/users/<int:user_id>/avatar', views.UserAvatarRetrieveView.as_view(), name='user-avatar-retrieve'),
    path('api/v1/users/password-rules', views.PasswordRulesView.as_view(), name='user-password-rules'),
    path('api/v1/users/groups', views.UserGroupsView.as_view(), name='user-groups'),
    # Settings
    path('api/v1/settings', views.SettingsListView.as_view(), name='settings-list'),
    path('api/v1/settings/<str:module>/<str:key>', views.SettingDetailView.as_view(), name='setting-detail'),
]
