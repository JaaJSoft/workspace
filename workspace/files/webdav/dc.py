"""Domain controller for WsgiDAV using Django's auth backend."""

from django.contrib.auth import authenticate
from wsgidav.dc.base_dc import BaseDomainController


class DjangoBasicDomainController(BaseDomainController):
    """Authenticate WebDAV requests via Django's ``authenticate()``."""

    def __init__(self, wsgidav_app, config):
        super().__init__(wsgidav_app, config)

    def get_domain_realm(self, path_info, environ):
        return "Workspace"

    def require_authentication(self, realm, environ):
        return True

    def supports_http_digest_auth(self):
        return False

    def basic_auth_user(self, realm, user_name, password, environ):
        user = authenticate(username=user_name, password=password)
        if user is None or not user.is_active:
            return False
        environ["workspace.user"] = user
        return True
