"""Shared test fixtures. Tests need data/bible.sqlite (built by the setup script)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.bible_db import BibleDB  # noqa: E402
from app.reference_parser import BookIndex  # noqa: E402


@pytest.fixture(scope="session")
def db() -> Iterator[BibleDB]:
    if not config.BIBLE_DB.exists():
        pytest.skip("data/bible.sqlite is missing: run the setup script first")
    database = BibleDB()
    yield database
    database.close()


@pytest.fixture(scope="session")
def books() -> BookIndex:
    return BookIndex()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """API test client with settings saved to a temp file (never touches real settings)."""
    if not config.BIBLE_DB.exists():
        pytest.skip("data/bible.sqlite is missing: run the setup script first")
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config, "SETTINGS_JSON", tmp_path / "settings.json")
    monkeypatch.setattr(config, "setup_logging", lambda: None)
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
