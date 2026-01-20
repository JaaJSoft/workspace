"""
URL configuration for workspace project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

urlpatterns = [
    path('admin/', admin.site.urls),

    # Authentication
    path('login', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout', auth_views.LogoutView.as_view(), name='logout'),

    # OpenAPI schema and documentation
    path('schema/', login_required(SpectacularAPIView.as_view()), name='schema'),
    path('schema/swagger-ui/', login_required(SpectacularSwaggerView.as_view(url_name='schema')), name='swagger-ui'),
    path('schema/redoc/', login_required(SpectacularRedocView.as_view(url_name='schema')), name='redoc'),

    # Health check
    path('health/', include('health_check.urls')),

    # Apps
    path('api/v1/files/', include('workspace.files.urls')),
    path('', include('workspace.dashboard.urls')),
    path('', include('workspace.users.urls')),
]

# Debug Toolbar URLs (only in DEBUG mode)
if __name__ != '__main__':
    from django.conf import settings

    if settings.DEBUG:
        import debug_toolbar

        urlpatterns = [
                          path('__debug__/', include(debug_toolbar.urls)),
                      ] + urlpatterns
