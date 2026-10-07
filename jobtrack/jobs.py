"""Background sync orchestration, independent of HTTP and UI concerns."""

from __future__ import annotations

import threading

from jobtrack.analyze import build_analyzer
from jobtrack.auth import AuthError, get_credentials
from jobtrack.config import TOKEN_PATH, resolve_credentials
from jobtrack.contracts import SyncRequest
from jobtrack.gmail_client import GmailClient, default_query
from jobtrack.models import gmail_thread_url
from jobtrack.settings import load as load_settings
from jobtrack.store import Store
from jobtrack.sync import now_iso, run_sync
from jobtrack.timerange import with_time_range


class SyncRunner:
    """Runs at most one sync at a time and exposes its progress."""

    def __init__(
        self,
        db_path: str,
        credentials: str | None = None,
        llm_mode: str | None = None,
        *,
        client_factory=GmailClient,
        credentials_loader=get_credentials,
        analyzer_factory=build_analyzer,
        pipeline=run_sync,
        settings=None,
    ):
        self.client_factory = client_factory
        self.credentials_loader = credentials_loader
        self.analyzer_factory = analyzer_factory
        self.pipeline = pipeline
        self.db_path = db_path
        self.credentials = credentials
        self.llm_mode = llm_mode
        self.settings = settings if settings is not None else load_settings()
        self._lock = threading.Lock()
        self._state: dict = {"status": "idle"}

    def snapshot(self) -> dict:
        with self._lock:
            state = dict(self._state)
            state["candidates"] = list(self._state.get("candidates", []))
        if state["status"] != "running":
            with Store(self.db_path) as store:
                history = store.sync_history(limit=1)
            if history and history[0]["id"] != state.get("id"):
                return history[0]
        return state

    def start(self, request: SyncRequest) -> bool:
        with self._lock:
            if self._state.get("status") == "running":
                return False
            query = with_time_range(
                request.query or default_query(),
                since=request.since,
                before=request.before,
            )
            with Store(self.db_path) as store:
                run_id = store.begin_sync(query, dry_run=request.dry_run)
            self._state = {
                "id": run_id,
                "dry_run": request.dry_run,
                "candidates": [],
                "status": "running",
                "scanned": 0,
                "started_at": now_iso(),
                "finished_at": None,
                "summary": None,
                "error": None,
                "note": None,
                "recheck": request.recheck,
            }
        threading.Thread(target=self._run, args=(request,), daemon=True).start()
        return True

    # -- thread body -------------------------------------------------------
    def _update(self, **fields) -> None:
        with self._lock:
            self._state.update(fields)

    def _fail(self, message: str) -> None:
        self._update(status="error", error=message, finished_at=now_iso(), summary=None)
        run_id = self._state.get("id")
        if run_id:
            with Store(self.db_path) as store:
                store.finish_sync(run_id, error=message)

    def _candidate(self, message, result, source) -> None:
        with self._lock:
            candidates = self._state.setdefault("candidates", [])
            if len(candidates) < 100:
                candidates.append(
                    {
                        "company": result.company or "Unknown",
                        "role": result.role,
                        "kind": result.kind,
                        "confidence": result.confidence,
                        "source": source,
                        "subject": message.subject,
                        "date": message.date.isoformat(),
                        "gmail_url": gmail_thread_url(message.thread_id),
                        "review": result.review,
                    }
                )

    def _track_sent(self, request: SyncRequest) -> bool:
        """Explicit request wins over the saved setting."""
        if request.track_sent is not None:
            return request.track_sent
        return bool(getattr(self.settings, "track_sent", False))
    def _run(self, request: SyncRequest) -> None:
        # Validate the cheapest, purely local input first, so a typo does not
        # cost a token refresh before being reported.
        try:
            query = with_time_range(
                request.query or default_query(),
                since=request.since,
                before=request.before,
            )
        except ValueError as exc:
            self._fail(str(exc))
            return

        try:
            analyzer = self.analyzer_factory(
                mode=self.llm_mode,
                on_note=lambda message: self._update(note=message),
            )

            credentials_path = resolve_credentials(self.credentials)
            creds = self.credentials_loader(
                credentials_path=credentials_path,
                token_path=TOKEN_PATH,
                interactive=False,
            )
            client = self.client_factory(creds)
            try:
                own_address = client.profile_email()
            except Exception:  # offline — not worth aborting the sync over
                own_address = None

            with Store(self.db_path) as store:
                summary = self.pipeline(
                    store,
                    client,
                    analyzer,
                    query=query,
                    limit=request.limit,
                    recheck=request.recheck,
                    own_address=own_address,
                    track_sent=self._track_sent(request),
                    since=request.since,
                    before=request.before,
                    dry_run=request.dry_run,
                    run_id=self._state.get("id"),
                    on_candidate=self._candidate if request.dry_run else None,
                    on_progress=lambda scanned: self._update(scanned=scanned),
                )
        except AuthError as exc:
            self._fail(str(exc))
        except Exception as exc:  # background thread: never die silently
            self._fail(f"{type(exc).__name__}: {exc}")
        else:
            if self._state.get("id"):
                with Store(self.db_path) as store:
                    store.finish_sync(self._state["id"], summary.to_dict())
            self._update(
                status="done",
                summary=summary.to_dict(),
                finished_at=now_iso(),
                scanned=summary.scanned,
            )
