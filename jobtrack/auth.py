"""Google OAuth for read-only Gmail access."""

from __future__ import annotations

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from jobtrack.config import CREDENTIALS_PATH, GMAIL_SCOPES, TOKEN_PATH


class AuthError(RuntimeError):
    """Raised when we cannot obtain usable Gmail credentials."""


def _load_token(token_path: Path) -> Credentials | None:
    if not token_path.is_file():
        return None
    try:
        return Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
    except (ValueError, json.JSONDecodeError):
        return None


def _save_token(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    token_path.chmod(0o600)


def get_credentials(
    credentials_path: Path | str = CREDENTIALS_PATH,
    token_path: Path | str = TOKEN_PATH,
    interactive: bool = True,
) -> Credentials:
    """Return valid Gmail credentials, refreshing or prompting as needed.

    When ``interactive`` is False a missing or unusable token raises instead of
    opening a browser, which keeps ``sync`` usable from cron.
    """
    credentials_path = Path(credentials_path)
    token_path = Path(token_path)

    creds = _load_token(token_path)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, token_path)
            return creds
        except Exception:  # refresh token revoked or offline
            creds = None

    if not interactive:
        raise AuthError(
            "Stored Gmail token is missing or expired. Run `jobtrack auth` first.\n"
            "If this happens roughly every week, your Google Cloud app is in "
            "'Testing' publishing status, where refresh tokens expire after 7 days. "
            "Switch it to 'In production' (no verification needed) and authorise "
            "again — see the README section 'Making the token last'."
        )

    if not credentials_path.is_file():
        raise AuthError(
            f"OAuth client file not found at {credentials_path}.\n"
            "Download it from Google Cloud Console (see README) or set "
            "$JOBTRACK_CREDENTIALS to its location."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path), GMAIL_SCOPES
    )
    creds = flow.run_local_server(port=0, open_browser=True)
    _save_token(creds, token_path)
    return creds


def revoke(token_path: Path | str = TOKEN_PATH) -> bool:
    """Delete the cached token. Returns True if there was one."""
    token_path = Path(token_path)
    if token_path.is_file():
        token_path.unlink()
        return True
    return False
