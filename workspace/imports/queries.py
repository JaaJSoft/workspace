from .models import ImportConnection, ImportJob, ImportLoginFlow


def user_connections_qs(user):
    """Connections the user owns - the only access rule the module has."""
    return ImportConnection.objects.filter(owner=user)


def user_jobs_qs(user):
    """Import jobs the user owns."""
    return ImportJob.objects.filter(connection__owner=user)


def user_login_flows_qs(user):
    """Sign-in flows the user started."""
    return ImportLoginFlow.objects.filter(owner=user)
