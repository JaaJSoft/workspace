"""Factory for the WsgiDAV application."""

from wsgidav.wsgidav_app import WsgiDAVApp

from .dc import DjangoBasicDomainController
from .provider import WorkspaceDAVProvider


def create_webdav_app():
    """Build and return a configured ``WsgiDAVApp``."""
    config = {
        "mount_path": "/dav",
        "provider_mapping": {"/": WorkspaceDAVProvider()},
        "http_authenticator": {
            "domain_controller": DjangoBasicDomainController,
            "accept_basic": True,
            "accept_digest": False,
            "default_to_digest": False,
        },
        "verbose": 1,
        "logging": {
            "enable": True,
            "enable_loggers": [],
        },
        "property_manager": True,
        "lock_storage": True,
        "dir_browser": {
            "enable": False,
        },
    }
    return WsgiDAVApp(config)
