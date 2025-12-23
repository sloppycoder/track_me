import os
from pathlib import Path

import pytest

from myphoto.services.photo_processing_service import PhotoProcessingService

# Allow Django database operations in async context (needed for Playwright tests with live_server)
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Load initial data for all tests"""
    with django_db_blocker.unblock():
        pass
        # call_command("loaddata", "tests/fixtures/batchjobs.json")


@pytest.fixture
def loaded_data(django_db_setup):
    """Fixture that ensures data is loaded"""
    # Data is already loaded by django_db_setup
    pass


@pytest.fixture
def processed_photos(db):
    """
    Process test photos into the database for UI tests.

    This fixture processes test photos from tests/test_photos/ directory
    into the test database for each test function.
    """
    test_photos_dir = Path(__file__).parent / "test_photos"

    if not test_photos_dir.exists():
        # No test photos available, skip processing
        return

    service = PhotoProcessingService()

    # Process all photos in test_photos directory
    photo_files = (
        list(test_photos_dir.glob("**/*.jpg"))
        + list(test_photos_dir.glob("**/*.JPG"))
        + list(test_photos_dir.glob("**/*.heic"))
    )

    for photo_file in photo_files:
        # Get relative path from test_photos directory
        relative_path = photo_file.relative_to(test_photos_dir)
        service.process_single_photo(str(relative_path), base_directory=str(test_photos_dir))
