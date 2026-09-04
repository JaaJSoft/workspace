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

from pathlib import Path

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.urls import include, path
from django.views.decorators.cache import cache_page
from django.views.static import serve
from django_prometheus import exports as prometheus_exports
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from mozilla_django_oidc import views as oidc_views

from workspace.core.metrics_auth import metrics_basic_auth
from workspace.core.views.health import LiveView, ReadyView, StartupView
from workspace.users.ui.views import WorkspaceLoginView

api_urlpatterns = [
    # OpenAPI schema and documentation
    path(
        "schema/",
        login_required(cache_page(3600)(SpectacularAPIView.as_view())),
        name="schema",
    ),
    path(
        "schema/swagger-ui/",
        login_required(SpectacularSwaggerView.as_view(url_name="schema")),
        name="swagger-ui",
    ),
    path(
        "schema/redoc/",
        login_required(SpectacularRedocView.as_view(url_name="schema")),
        name="redoc",
    ),
    # API endpoints
    path("", include("workspace.core.urls")),
    path("", include("workspace.files.urls")),
    path("", include("workspace.users.urls")),
    path("", include("workspace.dashboard.urls")),
    path("", include("workspace.chat.urls")),
    path("", include("workspace.calendar.urls")),
    path("", include("workspace.mail.urls")),
    path("", include("workspace.notifications.urls")),
    path("", include("workspace.projects.urls")),
    path("", include("workspace.ai.urls")),
    path("", include("workspace.imports.urls")),
    path("", include("workspace.vault.urls")),
]

ui_urlpatterns = [
    # UI apps
    path("files", include("workspace.files.ui.urls")),
    path("notes", include("workspace.notes.ui.urls")),
    path("users", include("workspace.users.ui.urls")),
    path("chat", include("workspace.chat.ui.urls")),
    path("meet", include("workspace.chat.ui.meet_urls")),
    path("calendar", include("workspace.calendar.ui.urls")),
    path("mail", include("workspace.mail.ui.urls")),
    path("projects", include("workspace.projects.ui.urls")),
    path("vault", include("workspace.vault.ui.urls")),
    path("imports", include("workspace.imports.ui.urls")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    # Authentication
    path("login", WorkspaceLoginView.as_view(), name="login"),
    path("logout", auth_views.LogoutView.as_view(), name="logout"),
    # OIDC (SSO) login. The views come from mozilla-django-oidc but its own
    # URLconf is not included: its paths carry trailing slashes, which APPEND_SLASH
    # = False turns into a 404 for the slash-less form used everywhere else here.
    # The route names are the library's - it reverses them to build redirect_uri.
    # Configure the IdP redirect_uri to {origin}/oidc/callback.
    path(
        "oidc/authenticate",
        oidc_views.OIDCAuthenticationRequestView.as_view(),
        name="oidc_authentication_init",
    ),
    path(
        "oidc/callback",
        oidc_views.OIDCAuthenticationCallbackView.as_view(),
        name="oidc_authentication_callback",
    ),
    path(
        "oidc/logout",
        oidc_views.OIDCLogoutView.as_view(),
        name="oidc_logout",
    ),
    # Service Worker (must be at root scope for push notifications)
    path(
        "sw.js",
        serve,
        {
            "path": "sw.js",
            "document_root": Path(__file__).resolve().parent / "core" / "static",
        },
        name="service-worker",
    ),
    # Web App Manifest (must be at root for PWA install)
    path(
        "manifest.json",
        serve,
        {
            "path": "manifest.json",
            "document_root": Path(__file__).resolve().parent / "core" / "static",
        },
        name="manifest",
    ),
    # Health probes (k8s)
    path("health/startup", StartupView.as_view(), name="health-startup"),
    path("health/live", LiveView.as_view(), name="health-live"),
    path("health/ready", ReadyView.as_view(), name="health-ready"),
    # Prometheus metrics — django_prometheus exports them unauthenticated
    path(
        "metrics",
        metrics_basic_auth(prometheus_exports.ExportToDjangoView),
        name="prometheus-django-metrics",
    ),
]

urlpatterns += api_urlpatterns
urlpatterns += ui_urlpatterns

# Debug Toolbar URLs (only in DEBUG mode)
if __name__ != "__main__":
    from django.conf import settings

    if settings.DEBUG:
        import debug_toolbar

        urlpatterns += [path("__debug__/", include(debug_toolbar.urls))]
