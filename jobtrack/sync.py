"""The Gmail sync pipeline, shared by the CLI and the web app."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from jobtrack.categories import infer_category
from jobtrack.classify import classify, outbound_application
from jobtrack.gmail_client import sent_query
from jobtrack.models import Classification, EmailMessage
from jobtrack.store import normalize_company, normalize_role
from jobtrack.timerange import with_time_range


@dataclass
class SyncSummary:
    scanned: int = 0
    created: int = 0
    matched: int = 0
    duplicate: int = 0
    skipped: int = 0
    sent: int = 0
    llm: int = 0
    applications: int = 0
    llm_disabled_reason: str | None = None
    reviewed: int = 0
    unresolved: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _own_message_ids(client, sent: str) -> set[str]:
    """Message IDs of the mailbox owner's mail matching the sent search."""
    if not callable(getattr(client, "iter_message_ids", None)):
        return set()
    try:
        return set(client.iter_message_ids(sent, limit=0))
    except Exception:
        # A failing search must not abort the whole sync.
        return set()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_sync(
    store,
    client,
    analyzer,
    *,
    query: str,
    limit: int = 0,
    recheck: bool = False,
    dry_run: bool = False,
    own_address: str | None = None,
    track_sent: bool = False,
    since: str | None = None,
    before: str | None = None,
    on_progress=None,
    on_candidate=None,
    run_id: int | None = None,
) -> SyncSummary:
    """Scan Gmail and fold the results into the store.

    ``own_address`` is the mailbox's own address. By default mail *sent* by the
    user is dropped: it is not a response to an application, and a model will
    happily attribute it to whoever the user was writing to. With
    ``track_sent`` the rules decide instead — an outbound application is
    recorded as the submission it is, and everything else the user sent is
    still skipped.

    ``on_progress(scanned)`` fires periodically, and ``on_candidate(message,
    result, source)`` fires for every message that would be recorded — which is
    how the CLI's ``--dry-run`` prints its preview.
    """
    summary = SyncSummary()
    own = (own_address or "").strip().lower()
    analysis_key = getattr(analyzer, "cache_key", "rules-v7")
    known = set() if recheck else store.synced_message_ids()
    queries = [query]
    if track_sent:
        # The sent search is small and specific, while the inbound one can
        # return thousands of ids. Run it first, or the message limit is spent
        # on inbound mail and the user's own applications are never read.
        sent = sent_query(with_time_range("", since=since, before=before))
        queries = [sent, query]
        if not recheck:
            # Earlier runs cached the user's own mail as ignored, which would
            # hide every application they sent. Only IDs are fetched to un-forget it.
            known -= _own_message_ids(client, sent)
    if run_id is None and not dry_run:
        run_id = store.begin_sync(query)

    def messages():
        # Search returns cheap IDs. Fetch bodies only for messages we need.
        if callable(getattr(client, "iter_message_ids", None)):
            seen_ids: set[str] = set()
            new_count = 0
            for search in queries:
                for message_id in client.iter_message_ids(search, limit=0):
                    if message_id in seen_ids:
                        continue
                    seen_ids.add(message_id)
                    if message_id not in known:
                        if limit and new_count >= limit:
                            return
                        new_count += 1
                    yield (
                        message_id,
                        None if message_id in known else client.get_message(message_id),
                    )
        else:
            for message in client.iter_messages(query, limit=limit):
                yield message.message_id, message

    try:
        # Gmail searches are newest first. Review fetched mail in receipt order so
        # a company acknowledgment can use the LinkedIn confirmation before it.
        fetched = []
        fetch_error = None
        try:
            for item in messages():
                fetched.append(item)
        except Exception as exc:
            fetch_error = exc
        fetched.sort(
            key=lambda item: (
                item[1]
                .date.replace(tzinfo=item[1].date.tzinfo or timezone.utc)
                .timestamp()
                if item[1]
                else float("-inf"),
                item[0],
            )
        )
        history = []
        for message_id, message in fetched:
            if message_id in known:
                summary.duplicate += 1
                continue
            summary.scanned += 1
            if summary.scanned % 10 == 0:
                if on_progress is not None:
                    on_progress(summary.scanned)
                if run_id is not None:
                    store.sync_progress(run_id, summary.scanned)
            # Bound per message, so a message that never reaches the analyzer
            # branch below cannot leave either name undefined.
            unresolved = False
            sent = own and message.sender_email.strip().lower() == own
            if sent:
                # The user's own mail is only recorded when the rules see an
                # application in it; anything else they sent stays ignored.
                result = outbound_application(message) if track_sent else None
                if result is None:
                    summary.skipped += 1
                    if not dry_run:
                        store.ignore_message(message_id, analysis_key)
                    continue
                summary.sent += 1
                source = "rules"
            else:
                if (
                    callable(getattr(analyzer, "set_context", None))
                    and analyzer.llm_active
                ):
                    identity = classify(message)
                    context = store.related_context(message, identity)
                    related = []
                    for prior, verdict in history:
                        same_role = (
                            not identity
                            or not identity.role
                            or not verdict.role
                            or normalize_role(identity.role) == normalize_role(verdict.role)
                        )
                        same_company = (
                            identity
                            and identity.company
                            and normalize_company(identity.company)
                            == normalize_company(verdict.company or "")
                        )
                        same_thread = (
                            message.thread_id and message.thread_id == prior.thread_id
                        )
                        same_category = infer_category(
                            message.sender, message.subject, message.snippet
                        ) == infer_category(prior.sender, prior.subject, prior.snippet)
                        if (
                            same_role
                            and same_category
                            and (same_thread or same_company)
                            and 0
                            <= (message.date - prior.date).total_seconds()
                            <= 14 * 86400
                        ):
                            related.append(
                                f"{prior.date.isoformat()} | {prior.sender} | {prior.subject[:200]} | employer={verdict.company}, role={verdict.role or 'unstated'}, event={verdict.kind} | {prior.snippet[:300]}"
                            )
                    analyzer.set_context(
                        (context + "\n" + "\n".join(related[-4:])).strip()[-4000:]
                    )
                result, source = analyzer.analyze(message)
                review = getattr(analyzer, "last_review", None)
                unresolved = bool(review and review["status"] == "unresolved")
                if review:
                    summary.reviewed += 1
                    summary.unresolved += int(unresolved)
                    if not dry_run:
                        store.save_review(message, review)
            if result:
                history.append((message, result))
                history = history[-200:]
            if result is None:
                summary.skipped += 1
                # A failed provider is not evidence that a message is irrelevant.
                if (
                    not dry_run
                    and not analyzer.failures
                    and not analyzer.disabled_reason
                    and not unresolved
                ):
                    if recheck:
                        store.remove_message(message_id)
                    store.ignore_message(message_id, analysis_key)
                continue
            if source == "llm":
                summary.llm += 1
            if on_candidate is not None:
                on_candidate(message, result, source)
            if dry_run:
                summary.created += 1
                continue
            if unresolved and recheck and store.has_message(message_id):
                # Disagreement is not grounds to overwrite a previously recorded event.
                summary.skipped += 1
                continue
            outcome, _ = store.record(message, result, replace=recheck)
            if outcome == "created":
                summary.created += 1
            else:
                summary.matched += 1
            known.add(message_id)

        if fetch_error:
            raise fetch_error

        summary.applications = len(store.list_applications())
        summary.llm_disabled_reason = analyzer.disabled_reason
        if not dry_run:
            store.set_meta("last_sync_at", now_iso())
        if run_id is not None:
            store.finish_sync(run_id, summary.to_dict())
        if on_progress is not None:
            on_progress(summary.scanned)
        return summary
    except Exception as exc:
        if run_id is not None:
            store.finish_sync(run_id, summary.to_dict(), f"{type(exc).__name__}: {exc}")
        raise


def describe_candidate(
    message: EmailMessage, result: Classification, source: str
) -> str:
    """One-line preview used by `sync --dry-run`."""
    return (
        f"{result.kind:<10} {(result.company or '?'):<24} "
        f"{source:<5} conf={result.confidence:<4} {message.subject[:52]}"
    )
