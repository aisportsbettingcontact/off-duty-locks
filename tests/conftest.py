import pathlib

import pytest

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    return FIXTURES_DIR
