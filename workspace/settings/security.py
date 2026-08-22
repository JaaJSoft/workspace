"""CSRF, cookies, proxy headers, authentication and password policy."""

from csp.constants import NONE, SELF, UNSAFE_EVAL

from .base import DEBUG, TESTING
from .env import env_bool, env_list

# CSRF Configuration for production
# Allow CSRF_TRUSTED_ORIGINS to be set via env (comma-separated list)
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# In production behind a proxy, ensure CSRF cookies work correctly
if not DEBUG:
    CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", default=True)
    CSRF_COOKIE_HTTPONLY = False  # Must be False for JavaScript to read it if needed
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", default=True)
    SECURE_PROXY_SSL_HEADER = (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )  # Trust X-Forwarded-Proto from proxy
    # Trust X-Forwarded-Host/Port — only enable when the proxy rewrites them (Cloudflare, cloud LBs)
    USE_X_FORWARDED_HOST = env_bool("USE_X_FORWARDED_HOST")
    USE_X_FORWARDED_PORT = env_bool("USE_X_FORWARDED_PORT")

# Login/Logout URLs
LOGIN_URL = "/login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login"

# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

if TESTING:
    PASSWORD_HASHERS = [
        "django.contrib.auth.hashers.MD5PasswordHasher",
    ]


# The vault module's own Content-Security-Policy, applied per view through
# csp.decorators.csp. Deliberately NOT a project-wide default: inline scripts
# and inline style attributes still live elsewhere in the project, and a global
# policy is a separate piece of work. The module holds the line the rest of the
# project cannot yet.
#
# api.pwnedpasswords.com is mandatory rather than decorative - the vault
# password strength floor queries it under k-anonymity, and connect-src 'self'
# would kill the feature while making the page merely look slow.
#
# unsafe-eval stays: Alpine 3 builds its expressions with new AsyncFunction().
# The @alpinejs/csp build exists but forbids inline expressions in attributes,
# which would mean rewriting every x-on: in the project.
# The directives themselves, not the DIRECTIVES-wrapped shape the global
# CONTENT_SECURITY_POLICY setting takes: csp.decorators.csp expects the mapping
# directly, and handing it the wrapper fails at request time, not at import.
VAULT_CSP = {
    "default-src": [NONE],
    "script-src": [SELF, UNSAFE_EVAL],
    "connect-src": [SELF, "https://api.pwnedpasswords.com"],
    "style-src": [SELF],
    "img-src": [SELF, "data:", "blob:"],
    "object-src": [NONE],
    "base-uri": [NONE],
    "form-action": [SELF],
    "frame-ancestors": [NONE],
}
