from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    DJANGO_LOG_LEVEL=(str, "INFO"),
    EMAIL_HOST=(str, "localhost"),
    EMAIL_PORT=(int, 25),
    EMAIL_HOST_USER=(str, ""),
    EMAIL_HOST_PASSWORD=(str, ""),
    EMAIL_USE_TLS=(bool, False),
    DEFAULT_FROM_EMAIL=(str, "SIGTCC <nao-responder@sigtcc.local>"),
    JWT_ACCESS_MINUTES=(int, 5),
    JWT_REFRESH_MINUTES=(int, 30),
    JWT_REFRESH_COOKIE_SECURE=(bool, True),
    JWT_TRUSTED_ORIGINS=(list, ["http://localhost:3000", "http://127.0.0.1:3000"]),
    FRONTEND_URL=(str, "http://localhost:3000"),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "ninja_extra",
    "ninja_jwt",
    "ninja_jwt.token_blacklist",
    "apps.accounts",
    "apps.advising",
    "apps.activities",
    "apps.meetings",
    "apps.notifications",
]

MIDDLEWARE = [
    "config.middleware.RequestLoggingMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {"default": env.db("DATABASE_URL")}

EMAIL_HOST = env("EMAIL_HOST")
EMAIL_PORT = env("EMAIL_PORT")
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = env("EMAIL_USE_TLS")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL")

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Santarem"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "config.logging.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL")},
    "loggers": {
        "sigtcc.request": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "sigtcc.auth": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

NINJA_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env("JWT_ACCESS_MINUTES")),
    "REFRESH_TOKEN_LIFETIME": timedelta(minutes=env("JWT_REFRESH_MINUTES")),
    "ROTATE_REFRESH_TOKENS": False,
    "UPDATE_LAST_LOGIN": True,
}
JWT_REFRESH_COOKIE_NAME = "refresh_token"
JWT_REFRESH_COOKIE_PATH = "/api/auth"
JWT_REFRESH_COOKIE_SECURE = env("JWT_REFRESH_COOKIE_SECURE")
JWT_TRUSTED_ORIGINS = env("JWT_TRUSTED_ORIGINS")

CACHES = {"default": env.cache_url("CACHE_URL", default="locmemcache://")}

# How many trusted proxies sit between the client and the application (0 = none). With N > 0 the
# client address is the N-th entry of X-Forwarded-For counted from the end, which the known proxy added.
NUM_PROXIES = env.int("NUM_PROXIES", default=0)


def _auth_rate(name: str, limit: int, window_seconds: int) -> tuple[int, int]:
    configured_limit = env.int(f"AUTH_{name}_LIMIT", default=limit)
    configured_window = env.int(f"AUTH_{name}_WINDOW_SECONDS", default=window_seconds)
    if configured_limit < 1 or configured_window < 1:
        raise ImproperlyConfigured(
            f"AUTH_{name}_LIMIT and AUTH_{name}_WINDOW_SECONDS must be at least 1."
        )
    return configured_limit, configured_window


# RNF25: (limit, window in seconds) per scope. Failure scopes count wrong secrets per account and
# client address; the *_ip scopes count every request of a client address.
AUTH_RATE_LIMITS = {
    "login_failures": _auth_rate("LOGIN_FAILURES", 5, 15 * 60),
    "change_password_failures": _auth_rate("CHANGE_PASSWORD_FAILURES", 5, 15 * 60),
    "login_ip": _auth_rate("LOGIN_IP", 20, 60),
    "refresh_ip": _auth_rate("REFRESH_IP", 30, 60),
    "logout_ip": _auth_rate("LOGOUT_IP", 30, 60),
    "reset_ip": _auth_rate("RESET_IP", 10, 60 * 60),
    "forgot_ip": _auth_rate("FORGOT_IP", 5, 60 * 60),
}

PASSWORD_RESET_TIMEOUT = 60 * 60
FRONTEND_URL = env("FRONTEND_URL")

NINJA_PAGINATION_PER_PAGE = 20
NINJA_PAGINATION_MAX_LIMIT = 100
