"""CSRF, cookies, proxy headers, authentication and password policy."""

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
