"""Persistent settings, editable from the web UI.

Kept in its own file rather than in the `meta` table on purpose: `jobtrack reset`
deletes the database, and losing your API keys along with the data would be a
nasty surprise.

Precedence for anything the model needs is:

    command-line flag  >  this file  >  process environment  >  .env  >  default

The file wins over the environment because it is the explicit, in-app choice —
typing a key into the settings page and seeing it ignored would be baffling.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from jobtrack.config import DATA_DIR

SETTINGS_PATH = DATA_DIR / "settings.json"
ENV_PATH = Path.cwd() / ".env"
WRITE_LOCK = threading.RLock()

# Fields the settings form is allowed to write.
EDITABLE_FIELDS = (
    "llm_provider",
    "llm_model",
    "llm_base_url",
    "llm_mode",
    "sync_since",
    "track_sent",
)


@dataclass
class Settings:
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_mode: str | None = None
    sync_since: str | None = None
    # Off by default: importing the user's own applications is a one-time,
    # visible change they opt into.
    track_sent: bool = False
    # Per provider, so switching back and forth does not lose keys.
    llm_api_keys: dict[str, str] = field(default_factory=dict)
    # Provider endpoints and an ordered team; legacy single-model settings remain valid.
    provider_urls: dict[str, str] = field(default_factory=dict)
    review_models: list[dict[str, str]] = field(default_factory=list)
    review_strategy: str = "single"
    review_rounds: int = 2

    @classmethod
    def from_dict(cls, raw: dict) -> "Settings":
        if not isinstance(raw, dict):
            return cls()
        keys = raw.get("llm_api_keys")
        if not isinstance(keys, dict):
            keys = {}
        return cls(
            llm_provider=_text(raw.get("llm_provider")),
            llm_model=_text(raw.get("llm_model")),
            llm_base_url=_text(raw.get("llm_base_url")),
            llm_mode=_text(raw.get("llm_mode")),
            sync_since=_text(raw.get("sync_since")),
            track_sent=bool(raw.get("track_sent", False)),
            llm_api_keys={
                str(k): str(v)
                for k, v in (keys or {}).items()
                if isinstance(v, str) and v.strip()
            },
            provider_urls=raw.get("provider_urls")
            if isinstance(raw.get("provider_urls"), dict)
            else {},
            review_models=raw.get("review_models")
            if isinstance(raw.get("review_models"), list)
            else [],
            review_strategy=raw.get("review_strategy", "single"),
            review_rounds=raw.get("review_rounds", 2),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def mask(value: str | None) -> str | None:
    """Show enough of a secret to recognise it, never enough to use it."""
    if not value:
        return None
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def load() -> Settings:
    """Read the settings file. Missing or corrupt files mean defaults."""
    if not SETTINGS_PATH.is_file():
        return Settings()
    try:
        return Settings.from_dict(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return Settings()


def save(settings: Settings) -> None:
    """Write atomically, readable only by this user."""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=".settings-", suffix=".json.tmp", dir=SETTINGS_PATH.parent
    )
    tmp = type(SETTINGS_PATH)(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(settings.to_dict(), indent=2) + "\n")
        tmp.chmod(0o600)
        tmp.replace(SETTINGS_PATH)
    finally:
        tmp.unlink(missing_ok=True)


def api_key_for(settings: Settings, provider: str) -> str | None:
    return settings.llm_api_keys.get(provider) or None


def environment_value(name: str, default: str | None = None) -> str | None:
    """Read process overrides, then this workspace's .env without exporting secrets.

    Read on demand so replacing a key needs no server restart. Disable variable
    interpolation: credentials containing dollar signs must remain literal.
    """
    if name in os.environ:
        return os.environ[name]
    return dotenv_values(ENV_PATH, interpolate=False).get(name) or default


def key_source(settings: Settings, provider: str, key_env: str | None) -> str | None:
    """Where the key in effect came from — shown in the UI so it is never a mystery."""
    if api_key_for(settings, provider):
        return "settings"
    for variable in (key_env, "JOBTRACK_LLM_API_KEY"):
        if variable and environment_value(variable):
            return "environment" if os.environ.get(variable) else ".env"
    return None
