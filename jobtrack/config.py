"""Filesystem locations for config, token and database files."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "jobtrack"


def _platform_data_dir() -> Path:
    override = os.environ.get("JOBTRACK_HOME")
    if override:
        return Path(override).expanduser()

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME

    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


DATA_DIR = _platform_data_dir()
DB_PATH = DATA_DIR / "jobtrack.db"
TOKEN_PATH = DATA_DIR / "token.json"
CREDENTIALS_PATH = DATA_DIR / "credentials.json"

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# The web UI is local-only by default: it serves your email data with no
# authentication, so binding it to a network interface must be deliberate.
WEB_HOST = "127.0.0.1"
WEB_PORT = 8765


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def resolve_credentials(explicit: str | None = None) -> Path:
    """Find the OAuth client secret file.

    Order: explicit argument, $JOBTRACK_CREDENTIALS, the data dir, then cwd.
    """
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get("JOBTRACK_CREDENTIALS")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(CREDENTIALS_PATH)
    candidates.append(Path.cwd() / "credentials.json")

    for path in candidates:
        if path.is_file():
            return path

    return candidates[0] if explicit else CREDENTIALS_PATH
