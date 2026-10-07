"""Local web UI: browse applications and jump straight into Gmail.

HTTP composition for the local workspace. Classification and import live in
analyze/sync, background execution in jobs, migrations in schema, and dashboard
read models in workspace. SQLite remains the single source of truth.
"""

from __future__ import annotations

import csv
import io
import os
import sqlite3
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from jobtrack import settings
from jobtrack.analyze import MODES, build_analyzer
from jobtrack.auth import get_credentials
from jobtrack.config import (
    DB_PATH,
    TOKEN_PATH,
    WEB_HOST,
    WEB_PORT,
    ensure_data_dir,
)
from jobtrack.contracts import ApplicationUpdate, SettingsUpdate, SyncRequest
from jobtrack.gmail_client import GmailClient
from jobtrack.jobs import SyncRunner as BackgroundSync
from jobtrack.llm import (
    PROVIDER_DEFAULTS,
    SAMPLE_EMAIL,
    LLMConfig,
    LLMError,
    available_models,
    build_llm,
    classify_message,
)
from jobtrack.models import (
    ACTIVE_STATUSES,
    STATUS_LABELS,
    Application,
    Event,
    gmail_thread_url,
)
from jobtrack.roles import build_categorizer
from jobtrack.settings import EDITABLE_FIELDS
from jobtrack.store import Store, SyncBusyError
from jobtrack.sync import run_sync
from jobtrack.timerange import as_of_bound, parse_date, with_time_range
from jobtrack.workspace import overview

INDEX_PATH = Path(__file__).parent / "web" / "index.html"

DEFAULT_HOST = WEB_HOST
DEFAULT_PORT = WEB_PORT


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------
def _serialize_event(event: Event) -> dict:
    return {
        "id": event.id,
        "kind": event.kind,
        "label": STATUS_LABELS.get(event.kind, event.kind.title()),
        "date": event.event_date.isoformat(),
        "subject": event.subject,
        "sender": event.sender,
        "snippet": event.snippet,
        "confidence": event.confidence,
        "source": event.source,
        "gmail_url": event.gmail_url,
        "review": event.review,
    }


def _serialize_application(app: Application) -> dict:
    return {
        "id": app.id,
        "company": app.company,
        "role": app.role,
        "status": app.status,
        "status_label": app.status_label,
        "first_seen": app.first_seen.isoformat(),
        "last_event_at": app.last_event_at.isoformat(),
        "event_count": app.event_count,
        "notes": app.notes,
        "category": app.category,
        "follow_up_on": app.follow_up_on,
        "status_override": app.status_override,
    }


# --------------------------------------------------------------------------
# Background sync
# --------------------------------------------------------------------------
class SyncRunner(BackgroundSync):
    """Compose the background service with the web app's integrations."""

    def __init__(self, db_path, credentials=None, llm_mode=None):
        super().__init__(
            db_path,
            credentials,
            llm_mode,
            client_factory=lambda creds: GmailClient(creds),
            credentials_loader=lambda **kwargs: get_credentials(**kwargs),
            analyzer_factory=lambda **kwargs: build_analyzer(**kwargs),
            pipeline=lambda *args, **kwargs: run_sync(*args, **kwargs),
            settings=settings.load(),
        )


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
def _resolve_as_of(as_of: str | None) -> datetime | None:
    """Parse an ?as_of= date, or 400 if it is not one."""
    if not as_of:
        return None
    try:
        return as_of_bound(as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _settings_payload() -> dict:
    """Everything the settings form needs, with secrets masked."""
    stored = settings.load()
    effective = LLMConfig.load()
    provider_config = PROVIDER_DEFAULTS.get(effective.provider, ("", "", None))
    key_env = provider_config[2]

    return {
        "llm_provider": effective.provider,
        "llm_model": stored.llm_model or "",
        "llm_base_url": stored.llm_base_url or "",
        "llm_mode": stored.llm_mode or os.environ.get("JOBTRACK_LLM_MODE") or "auto",
        "sync_since": stored.sync_since or "",
        "track_sent": stored.track_sent,
        "effective": {
            "provider": effective.provider,
            "model": effective.model,
            "base_url": effective.base_url,
            "api_key_set": bool(effective.api_key),
            "api_key_hint": settings.mask(effective.api_key),
            "api_key_source": settings.key_source(stored, effective.provider, key_env),
            "is_local": effective.is_local,
        },
        "providers": ["nvidia"] + sorted(p for p in PROVIDER_DEFAULTS if p != "nvidia"),
        "providers_with_keys": sorted(stored.llm_api_keys),
        "provider_defaults": {
            name: {"model": model, "base_url": base, "key_env": key}
            for name, (base, model, key) in PROVIDER_DEFAULTS.items()
        },
        "modes": list(MODES),
        "review_models": stored.review_models
        or [{"provider": effective.provider, "model": effective.model}],
        "review_strategy": stored.review_strategy,
        "review_rounds": stored.review_rounds,
        "connections": {
            name: {
                "api_key_hint": settings.mask(LLMConfig.for_provider(name).api_key),
                "api_key_set": bool(LLMConfig.for_provider(name).api_key),
                "api_key_source": settings.key_source(stored, name, key),
                "base_url": LLMConfig.for_provider(name).base_url,
                "is_local": LLMConfig.for_provider(name).is_local,
            }
            for name, (_, _, key) in PROVIDER_DEFAULTS.items()
        },
    }


def _config_for(provider: str) -> LLMConfig:
    """Config for probing one provider, using whatever key we already have."""
    return LLMConfig.for_provider(provider)


def create_app(
    db_path: str | Path | None = None,
    credentials: str | None = None,
    llm_mode: str | None = None,
    role_categorizer=None,
) -> FastAPI:
    ensure_data_dir()
    db = str(db_path or DB_PATH)
    runner = SyncRunner(db, credentials=credentials, llm_mode=llm_mode)

    app = FastAPI(
        title="jobtrack",
        description="Local job application tracker",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.mount("/assets", StaticFiles(directory=INDEX_PATH.parent), name="assets")

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
            if origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse(
                    {"detail": "Cross-origin writes are not allowed"}, status_code=403
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(INDEX_PATH.read_text(encoding="utf-8"))

    @app.get("/api/applications")
    def list_applications(
        status: list[str] | None = Query(default=None),
        search: str | None = None,
        active: bool = False,
        as_of: str | None = None,
        category: Literal["employment", "freelance"] | None = None,
    ):
        bound = _resolve_as_of(as_of)
        if status and any(value not in STATUS_LABELS for value in status):
            raise HTTPException(status_code=400, detail="Unknown status")
        statuses = (
            [value for value in (status or ACTIVE_STATUSES) if value in ACTIVE_STATUSES]
            if active
            else status
        )
        if active and status and not statuses:
            return {
                "applications": [],
                "as_of": parse_date(as_of).isoformat() if bound else None,
            }
        with Store(db) as store:
            applications = store.list_applications(
                statuses=statuses, search=search, as_of=bound, category=category
            )
            return {
                "applications": [_serialize_application(a) for a in applications],
                "as_of": parse_date(as_of).isoformat() if bound else None,
            }

    @app.get("/api/export")
    def export_applications(
        category: Literal["employment", "freelance"],
        as_of: str | None = None,
        ids: str | None = None,
    ):
        with Store(db) as store:
            applications = store.list_applications(
                category=category, as_of=_resolve_as_of(as_of)
            )
        if ids is not None:
            try:
                selected = [int(value) for value in ids.split(",") if value]
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="Invalid application IDs"
                ) from exc
            by_id = {a.id: a for a in applications}
            applications = [by_id[i] for i in dict.fromkeys(selected) if i in by_id]
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "Category",
                "Company",
                "Role",
                "Status",
                "First seen",
                "Last activity",
                "Messages",
                "Follow-up",
                "Notes",
            ]
        )

        def safe(value):
            text = str(value or "")
            return (
                "'" + text
                if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
                else text
            )

        for a in applications:
            writer.writerow(
                [
                    safe(value)
                    for value in (
                        a.category,
                        a.company,
                        a.role,
                        a.status_label,
                        a.first_seen.isoformat(),
                        a.last_event_at.isoformat(),
                        a.event_count,
                        a.follow_up_on,
                        a.notes,
                    )
                ]
            )
        suffix = "-" + parse_date(as_of).isoformat() if as_of else ""
        return Response(
            content="\ufeff" + output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="jobtrack-{category}{suffix}.csv"'
            },
        )

    @app.get("/api/applications/{app_id}")
    def application_detail(app_id: int, as_of: str | None = None):
        bound = _resolve_as_of(as_of)
        with Store(db) as store:
            application = store.get_application(app_id, as_of=bound)
            if application is None:
                raise HTTPException(status_code=404, detail="No such application")
            events = store.get_events(app_id, as_of=bound)
        return {
            "application": _serialize_application(application),
            "events": [_serialize_event(e) for e in events],
            "as_of": parse_date(as_of).isoformat() if bound else None,
        }

    @app.patch("/api/applications/{app_id}")
    def update_application(app_id: int, payload: ApplicationUpdate):
        updates = payload.model_dump(exclude_unset=True)
        for key in ("company", "role", "notes"):
            if key in updates:
                updates[key] = (updates[key] or "").strip()
        if updates.get("follow_up_on"):
            try:
                updates["follow_up_on"] = parse_date(
                    updates["follow_up_on"]
                ).isoformat()
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            with Store(db) as store:
                application = store.update_application(app_id, updates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="An application with this company and role already exists",
            ) from exc
        if application is None:
            raise HTTPException(status_code=404, detail="No such application")
        return {"application": _serialize_application(application)}

    @app.get("/api/overview")
    def workspace_overview(
        as_of: str | None = None,
        category: Literal["employment", "freelance"] | None = None,
    ):
        with Store(db) as store:
            return overview(
                store, _resolve_as_of(as_of), category=category,
                categorizer=role_categorizer,
            )

    @app.get("/api/activity")
    def activity(
        as_of: str | None = None,
        limit: int = Query(default=100, ge=1, le=200),
        category: Literal["employment", "freelance"] | None = None,
    ):
        with Store(db) as store:
            rows = store.recent_events(
                limit=limit, as_of=_resolve_as_of(as_of), category=category
            )
        return {
            "events": [
                {
                    "id": row["id"],
                    "application_id": row["application_id"],
                    "company": row["company"],
                    "role": row["role"],
                    "kind": row["kind"],
                    "date": row["event_date"],
                    "subject": row["subject"],
                    "snippet": row["snippet"],
                    "gmail_url": gmail_thread_url(row["thread_id"]),
                }
                for row in rows
            ]
        }

    @app.get("/api/sync/history")
    def sync_history():
        with Store(db) as store:
            return {"runs": store.sync_history()}

    @app.get("/api/health")
    def health():
        with Store(db) as store:
            version = store.conn.execute("PRAGMA user_version").fetchone()[0]
        return {
            "ok": True,
            "database_version": version,
            "gmail_token_saved": TOKEN_PATH.is_file(),
        }

    @app.get("/api/stats")
    def stats(
        as_of: str | None = None,
        category: Literal["employment", "freelance"] | None = None,
    ):
        bound = _resolve_as_of(as_of)
        with Store(db) as store:
            counts = store.status_counts(as_of=bound, category=category)
            return {
                "statuses": counts,
                "labels": STATUS_LABELS,
                "events": store.total_events(as_of=bound, category=category),
                "applications": sum(counts.values()),
                "last_sync": store.get_meta("last_sync_at"),
                "as_of": parse_date(as_of).isoformat() if bound else None,
            }

    @app.post("/api/sync")
    def start_sync(request: SyncRequest):
        if "since" not in request.model_fields_set:
            request.since = settings.load().sync_since or "365"
        try:
            started = runner.start(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SyncBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not started:
            raise HTTPException(status_code=409, detail="A sync is already running")
        return runner.snapshot()

    @app.get("/api/sync")
    def sync_status():
        return runner.snapshot()

    # -- settings ----------------------------------------------------------
    @app.get("/api/settings")
    def read_settings():
        return _settings_payload()

    @app.put("/api/settings")
    def write_settings(payload: SettingsUpdate):
        with settings.WRITE_LOCK:
            stored = settings.load()
            updates = payload.model_dump(exclude_unset=True)
            if (
                "llm_provider" in updates or "llm_model" in updates
            ) and "review_models" not in updates:
                # Older clients still select a single reviewer with these fields.
                stored.review_models = []
                stored.review_strategy = "single"

            if "llm_provider" in updates:
                provider = updates.pop("llm_provider") or ""
                if provider and provider not in PROVIDER_DEFAULTS:
                    raise HTTPException(
                        status_code=400, detail=f"Unknown provider {provider!r}"
                    )
                stored.llm_provider = provider or None

            if "llm_mode" in updates:
                mode = updates.pop("llm_mode") or ""
                if mode and mode not in MODES:
                    raise HTTPException(
                        status_code=400, detail=f"Unknown mode {mode!r}"
                    )
                stored.llm_mode = mode or None

            if "api_key" in updates:
                key = (updates.pop("api_key") or "").strip()
                target = stored.llm_provider or LLMConfig.load().provider
                if key:
                    stored.llm_api_keys[target] = key
                else:
                    stored.llm_api_keys.pop(target, None)

            if "connections" in updates:
                for provider, connection in (updates.pop("connections") or {}).items():
                    if provider not in PROVIDER_DEFAULTS:
                        raise HTTPException(
                            status_code=400, detail=f"Unknown provider {provider!r}"
                        )
                    if "api_key" in connection:
                        key = (connection["api_key"] or "").strip()
                        if key:
                            stored.llm_api_keys[provider] = key
                        else:
                            stored.llm_api_keys.pop(provider, None)
                    if "base_url" in connection:
                        url = (connection["base_url"] or "").strip()
                        if url:
                            stored.provider_urls[provider] = url
                        else:
                            stored.provider_urls.pop(provider, None)
                        if provider == (
                            stored.llm_provider or LLMConfig.load().provider
                        ):
                            stored.llm_base_url = url or None
            if "review_models" in updates:
                models = updates.pop("review_models") or []
                for model in models:
                    model["model"] = model["model"].strip()
                    if model["provider"] not in PROVIDER_DEFAULTS or not model["model"]:
                        raise HTTPException(
                            status_code=400,
                            detail="Choose a supported provider and non-empty model ID",
                        )
                if len({(m["provider"], m["model"]) for m in models}) != len(models):
                    raise HTTPException(
                        status_code=400, detail="Review models must be distinct"
                    )
                stored.review_models = models
            if "review_strategy" in updates:
                strategy = updates.pop("review_strategy")
                if strategy not in ("single", "consensus"):
                    raise HTTPException(
                        status_code=400, detail="Choose single or consensus review"
                    )
                stored.review_strategy = strategy
            if "review_rounds" in updates:
                stored.review_rounds = updates.pop("review_rounds") or 2
            if (
                stored.review_strategy == "consensus"
                and not 2 <= len(stored.review_models) <= 5
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Consensus needs two to five distinct models",
                )

            for field in EDITABLE_FIELDS:
                if field in updates:
                    value = updates[field]
                    if isinstance(getattr(stored, field), bool):
                        # A boolean must not be stringified and re-read as None.
                        setattr(stored, field, bool(value))
                        continue
                    setattr(stored, field, (value or "").strip() or None)

            if stored.sync_since:
                try:
                    with_time_range("", since=stored.sync_since)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            for endpoint in [stored.llm_base_url, *stored.provider_urls.values()]:
                if not endpoint:
                    continue
                from urllib.parse import urlparse

                url = urlparse(endpoint)
                if (
                    url.scheme not in ("http", "https")
                    or not url.netloc
                    or url.username
                    or url.password
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="Base URL must be an HTTP or HTTPS URL without credentials",
                    )
            try:
                settings.save(stored)
            except OSError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Could not write {settings.SETTINGS_PATH}: {exc}",
                ) from exc
            return _settings_payload()

    @app.get("/api/reviews")
    def review_history(status: Literal["agreed", "unresolved"] | None = None):
        with Store(db) as store:
            return {"reviews": store.recent_reviews(status)}

    @app.get("/api/settings/models")
    def provider_models(provider: str | None = None):
        """Ask the provider what it offers today, so the UI never offers dead IDs."""
        target = provider or LLMConfig.load().provider
        if target not in PROVIDER_DEFAULTS:
            raise HTTPException(status_code=400, detail=f"Unknown provider {target!r}")
        models = available_models(_config_for(target))
        return {"provider": target, "models": models}

    @app.post("/api/settings/test")
    def test_settings():
        """Read the sample email with the saved configuration, right now."""
        stored = settings.load()
        if stored.review_strategy == "consensus":
            from jobtrack.council import configured_council

            try:
                council = configured_council(stored, factory=build_llm)
                result = council.review(SAMPLE_EMAIL)
            except LLMError as exc:
                return {"ok": False, "error": str(exc)}
            return {
                "ok": result is not None,
                "error": None if result else council.last_review["resolution"],
                "review": council.last_review,
                "sample": {
                    "event": result.kind,
                    "company": result.company,
                    "role": result.role,
                    "confidence": result.confidence,
                }
                if result
                else None,
            }
        primary = stored.review_models[0] if stored.review_models else None
        config = (
            LLMConfig.for_provider(primary["provider"], primary["model"])
            if primary
            else LLMConfig.load()
        )
        try:
            llm = build_llm(config)
        except LLMError as exc:
            return {"ok": False, "error": str(exc)}

        usable, explanation = llm.check()
        if not usable:
            return {"ok": False, "error": explanation}

        try:
            result = classify_message(SAMPLE_EMAIL, llm)
        except LLMError as exc:
            return {"ok": False, "error": str(exc)}

        if result is None:
            return {
                "ok": False,
                "error": "The model replied but found no job event in the sample email.",
            }
        return {
            "ok": True,
            "message": explanation,
            "sample": {
                "event": result.kind,
                "company": result.company,
                "role": result.role,
                "confidence": result.confidence,
            },
        }

    return app


def serve(
    db_path: str | Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    credentials: str | None = None,
    llm_mode: str | None = None,
    open_browser: bool = True,
) -> None:
    """Run the UI until interrupted."""
    import uvicorn

    app = create_app(
        db_path,
        credentials=credentials,
        llm_mode=llm_mode,
        role_categorizer=build_categorizer(),
    )

    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{display_host}:{port}/"

    if open_browser:
        threading.Timer(1.0, lambda: _open_browser(url)).start()

    uvicorn.run(app, host=host, port=port, log_level="warning")


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # headless machines: not worth failing over
        pass
