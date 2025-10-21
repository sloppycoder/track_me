import json
import os
from pathlib import Path
from typing import Callable
import pytest
import logging







mock_data_path = Path(__file__).parent / "mockdata"


@pytest.fixture(scope="session")
def mock_file_content() -> Callable:
    def _mock_file_content(file_name):
        with open(mock_data_path / file_name, "r") as f:
            return f.read()

    return _mock_file_content







