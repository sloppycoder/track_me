import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
load_dotenv(dotenv_path=BASE_DIR / ".env")
devkey = "django-insecure-7@l%ik7thl2qo+8#zm%^6e(+72c!1310tujddhw2bgqk6f)r7m"
SECRET_KEY = os.environ.get("SECRET_KEY", devkey)
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")

# Photo thumbnail settings
THUMBNAIL_CACHE_DIR = Path(os.getenv("THUMBNAIL_CACHE_DIR", str(BASE_DIR / "tmp" / "thumbnails")))
THUMBNAIL_SIZE = (
    int(os.getenv("THUMBNAIL_WIDTH", "300")),
    int(os.getenv("THUMBNAIL_HEIGHT", "200")),
)
MAX_PHOTOS_PER_PAGE = int(os.getenv("MAX_PHOTOS_PER_PAGE", "200"))
PHOTOS_BASE_DIR = os.path.expanduser(os.getenv("PHOTOS_BASE_DIR", "~/tmp"))

# Footprints feature settings
MAX_FOOTPRINT_STEPS = int(os.getenv("MAX_FOOTPRINT_STEPS", "20"))
MIN_FOOTPRINT_STEPS = int(os.getenv("MIN_FOOTPRINT_STEPS", "3"))

# Ensure thumbnail cache directory exists
THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_HOSTS = [".localhost", "127.0.0.1", ".run.app", ".vino9.net"]
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8000",
    "https://*.vino9.net",
]
# DEBUGS defaults to True for development, required for serving static files
DEBUG = os.getenv("DEBUG", "0") == "1"


# Application definition

INSTALLED_APPS = [
    # "django.contrib.admin",  # Disabled - not needed
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_tailwind_cli",
    # New clean apps (rebuild)
    "library",
    # Legacy app (UI only, being ported then removed)
    "myphoto",
]

TAILWIND_CLI_VERSION = "2.7.5"  # use tailwind_extra cli that supports DaisyUI
TAILWIND_CLI_USE_DAISY_UI = True
TAILWIND_CLI_DIST_CSS = "css/tailwind.css"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
]

ROOT_URLCONF = "track_me.urls"


TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "track_me.wsgi.application"

# Local-first: default to a SQLite file. Set DATABASE_URL to override (e.g. a
# Cloud SQL Postgres instance when deploying to Cloud Run).
database_config = dj_database_url.config(
    default=f"sqlite:///{BASE_DIR / 'track_me.db'}",
    conn_max_age=600,
    conn_health_checks=True,
)

# Apply Postgres-only connection options only when actually using Postgres.
if str(database_config.get("ENGINE", "")).endswith("postgresql"):
    database_config["OPTIONS"] = {
        "sslmode": "require",
        "connect_timeout": 5,
        "options": "-c statement_timeout=10000",
    }

DATABASES = {"default": database_config}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

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


LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Singapore"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "assets"]
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        },
        "simple": {
            "format": "[{levelname}] {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "myphoto": {
            "level": "DEBUG",
            "propagate": True,
        },
    },
}

# Django-Q Configuration
# Q_CLUSTER = {
#     "name": "tracke_me_worker",
#     "workers": 1,
#     "timeout": 300,  # Task timeout in seconds
#     "retry": 315360000,  # 10 years, effectively never. set to -1 will trigger warning
#     "catch_up": False,
#     "cpu_affinity": 1,
#     "label": "edgar_viewer",
#     "orm": "default",  # Use database instead of Redis
# }
