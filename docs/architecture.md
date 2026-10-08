# Architecture

How a message becomes an application, and what the database guarantees while it
happens.

```text
Gmail search IDs → download unseen messages → classify / analyze → transactional Store
                                                                       ↓
                                                      SQLite applications + events + review audit
                                                                       ↓
                                                 workspace read models → HTTP API
                                                                       ↓
                                                      browser modules / CLI
```

| Module | Responsibility |
| --- | --- |
| `gmail_client.py` | Gmail search, bounded retries, MIME decoding, and profile lookup |
| `classify.py` | Relevance, lifecycle signals, and employer/role extraction |
| `analyze.py` / `llm.py` / `council.py` | Rules/model arbitration, provider clients, concurrent consensus and failure fallback |
| `sync.py` | Shared import pipeline for CLI, scheduler, and browser |
| `jobs.py` | Background thread lifecycle, progress, previews, and dependency injection |
| `store.py` | SQLite queries, transactional event writes, application corrections, import reservations |
| `schema.py` | Serialized, additive, versioned database migrations |
| `workspace.py` | Overview metrics, weekly cohorts, attention cues, and activity read models |
| `categories.py` | Freelance evidence and category-aware application identity |
| `roles.py` | Role-title grouping onto shared categories, model-assisted with a rule fallback and a persisted cache |
| `contracts.py` | Validated HTTP request shapes |
| `web.py` | HTTP composition, settings endpoints, local security checks, static asset delivery |
| `web/app.js` | Workspace navigation and application/sync flows |
| `web/settings.js` | Provider and settings flows |
| `web/api.js` / `web/ui.js` | HTTP client, safe rendering, formatting, and shared visual elements |
| `web/styles.css` / `web/index.html` | Responsive design and semantic page structure |

No JavaScript build server, database daemon, external asset CDN, queue service,
or additional runtime dependency is required.

## Importing

LinkedIn submission templates and company recruiting emails are both searched.
New imports use Gmail's receipt timestamp (including milliseconds), process
fetched messages oldest first, and provide previous related events to model
reviewers. Matching uses thread and employer/role identity; close arrival times
alone never merge different applications. Each message keeps its own timeline
event even when two confirmations describe one application. The timeline
displays date, time, seconds, and local time zone. The batch being imported is
held in memory for chronological sorting; apply a message limit for very large
backfills.

Known event IDs are skipped before body download. Ignored IDs are cached by
classifier revision and model configuration; reclassification bypasses that
cache. A provider failure is never cached as an irrelevant verdict.

## Persistence and import invariants

- A Gmail message is recorded at most once, keyed by its message ID.
- Event replacement, derived status refresh, and orphan cleanup occur in one SQLite transaction. A failed replacement rolls back to the original event.
- A Gmail thread identifies an application first. Outside that thread, distinct known roles at a company stay separate. Missing identity is attached only when the match is unambiguous; unknown employers do not share a company-wide bucket.
- SQLite WAL allows reading during an import, with a 30-second busy timeout for competing short writes.
- A database reservation prevents the browser, CLI, and scheduler from importing simultaneously.
- Import history retains start/finish times, progress, summary, and failures. Runs whose local owner process has stopped are marked interrupted; retrying resumes safely through message deduplication.
- Your own mail is dropped unless **Track applications you sent** is on, in which case only an application-shaped subject is recorded, by rules, from the recipient rather than the sender.
- Reclassification can remove a false positive if the analyzer now decides it is unrelated. An unavailable model falls back to rules.
- Derived status follows the furthest recorded lifecycle stage: offer, rejection, interview, assessment, applied, outreach, incomplete. A manual correction takes precedence in the current view.

## Migrations

Migrations preserve existing application IDs and email history. Before upgrading
a personal database, use SQLite's backup API or `.backup` while the app is
running, or copy the database **and its WAL** after stopping all processes.
Restore the backup together with the corresponding source version to roll back.
Do not reset the database to migrate it.