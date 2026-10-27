"""Paths and environment-driven settings."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NATIVE_DIR = ROOT / "backend" / "calibrate" / "native"
DATA_DIR = Path(os.environ.get("ECOLENS_DATA", ROOT / "data"))
BUILD_DIR = DATA_DIR / "build"
PROFILE_PATH = DATA_DIR / "profile.json"
FRONTEND_DIR = ROOT / "frontend"

# Used only when there is no RAPL and the user did not pass --watts.
DEFAULT_ASSUMED_WATTS = 15.0
