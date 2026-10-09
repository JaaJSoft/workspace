"""Domain controller for WsgiDAV using Django's auth backend."""

from wsgidav.dc.base_dc import BaseDomainController

from workspace.common.dav.auth import authenticate_basic


class DjangoBasicDomainController(BaseDomainController):
    """Authenticate WebDAV requests through ``common.dav.auth``.

    The Basic password may also be a Knox API token - the only credential
    OIDC-managed accounts (no usable local password) can present.
    """

    def __init__(self, wsgidav_app, config):
        super().__init__(wsgidav_app, config)

    def get_domain_realm(self, path_info, environ):
        return "Workspace"

    def require_authentication(self, realm, environ):
        return True

    def supports_http_digest_auth(self):
        return False

    def basic_auth_user(self, realm, user_name, password, environ):
        user = authenticate_basic(user_name, password)
        if user is None:
            return False
        environ["workspace.user"] = user
        return True
