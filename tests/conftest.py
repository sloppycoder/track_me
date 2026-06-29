import os
import subprocess
from pathlib import Path

import pytest

# Allow Django database operations in async context (needed for live_server tests)
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


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
