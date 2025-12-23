# ruff: noqa: F405 F403
import logging
from pathlib import Path

from track_me.settings import *

# Test-specific overrides
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Point to test photos directory
TEST_DIR = Path(__file__).parent
PHOTOS_BASE_DIR = str(TEST_DIR / "test_photos")

# Test-specific settings
DEBUG = False
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",  # Fast hasher for tests
]

# Disable CSRF for Playwright UI tests
# CSRF protection interferes with Playwright tests using live_server
MIDDLEWARE = [m for m in MIDDLEWARE if "CsrfViewMiddleware" not in m]

# Disable logging during tests to reduce noise
LOGGING_CONFIG = None

logging.disable(logging.CRITICAL)
