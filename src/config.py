"""Central project configuration: file paths and environment-based settings.

Every module imports paths from here so that nothing is hardcoded twice and
the project works no matter which directory a script is launched from.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = the folder that contains `src/`
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
REPORTS_DIR = PROJECT_ROOT / "reports"
CHARTS_DIR = REPORTS_DIR / "charts"
SQL_DIR = PROJECT_ROOT / "sql"

# Load variables from .env (if present) into the process environment.
load_dotenv(PROJECT_ROOT / ".env")


def get_env(name: str, default: str | None = None, required: bool = False) -> str | None:
    """Read an environment variable, optionally failing loudly if it is missing."""
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(
            f"Environment variable '{name}' is not set. "
            "Copy .env.example to .env and fill in your values."
        )
    return value
