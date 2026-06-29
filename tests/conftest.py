import os
import subprocess
import sys
from pathlib import Path

import pytest

# Allow Django database operations in async context (needed for live_server tests)
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def pytest_collection_modifyitems(config, items):
    """Skip @pytest.mark.playwright tests off macOS (e.g. CI / remote Linux)."""
    if sys.platform == "darwin":
        return
    skip = pytest.mark.skip(reason="Playwright UI tests run only on macOS")
    for item in items:
        if "playwright" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _build_tailwind_css():
    """Build Tailwind CSS before tests so template-rendering tests have styling."""
    from django.conf import settings

    dist_css = Path(settings.STATICFILES_DIRS[0]) / settings.TAILWIND_CLI_DIST_CSS
    if not dist_css.exists():
        subprocess.run(
            ["python", "manage.py", "tailwind", "build"],
            check=True,
            capture_output=True,
        )


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Load initial data for all tests"""
    with django_db_blocker.unblock():
        pass
