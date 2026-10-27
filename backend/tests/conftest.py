import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend import config  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def programs():
    return json.loads((FIXTURES / "programs.json").read_text())


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Isolate profile + build output from the real data/ folder."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "BUILD_DIR", tmp_path / "build")
    monkeypatch.setattr(config, "PROFILE_PATH", tmp_path / "profile.json")
    return tmp_path
