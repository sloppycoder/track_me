# ruff: noqa: F405 F403
import logging

from track_me.settings import *

# Test-specific overrides
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "tmp/trackme_test.db",
        # "NAME": ":memory:",
    }
}

# Test-specific settings
DEBUG = False
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",  # Fast hasher for tests
]

# Disable logging during tests to reduce noise
LOGGING_CONFIG = None

logging.disable(logging.CRITICAL)
