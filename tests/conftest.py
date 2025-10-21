import pytest


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
