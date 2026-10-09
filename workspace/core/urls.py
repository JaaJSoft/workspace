from anymail import urls as anymail_urls
from django.urls import path

from workspace.core.views.activity import (
    ActivityDailyCountsView,
    ActivityRecentView,
    ActivityStatsView,
)
from workspace.core.views.changelog import changelog_partial
from workspace.core.views.csp_report import CspReportView
from workspace.core.views.email import requires_webhook_secret, unsubscribe
from workspace.core.views.modules import ModulesView
from workspace.core.views.search import UnifiedSearchView
from workspace.core.views.sse import global_stream

# Anymail's bounce and complaint webhooks, one per mail provider, without the
# trailing slash its own URLconf puts on them (APPEND_SLASH = False would turn
# it into a 404 for the form every other endpoint here uses). Its inbound-mail
# webhooks are left out: the instance receives no mail.
provider_webhook_urlpatterns = [
    path(
        f"api/v1/email/{str(pattern.pattern).removesuffix('/')}",
        requires_webhook_secret(pattern.callback),
        name=f"anymail-{pattern.name}",
    )
    for pattern in anymail_urls.urlpatterns
    if pattern.name.endswith("_tracking_webhook")
]

urlpatterns = [
    path("api/v1/modules", ModulesView.as_view(), name="modules-list"),
    path("api/v1/search", UnifiedSearchView.as_view(), name="unified-search"),
    path("api/v1/stream", global_stream, name="global-sse-stream"),
    path(
        "api/v1/activity/recents", ActivityRecentView.as_view(), name="activity-recent"
    ),
    path(
        "api/v1/activity/daily-counts",
        ActivityDailyCountsView.as_view(),
        name="activity-daily-counts",
    ),
    path("api/v1/activity/stats", ActivityStatsView.as_view(), name="activity-stats"),
    path("api/v1/csp-report", CspReportView.as_view(), name="csp-report"),
    *provider_webhook_urlpatterns,
    path("email/unsubscribe/<str:token>", unsubscribe, name="email-unsubscribe"),
    path("changelog", changelog_partial, name="changelog-partial"),
]
