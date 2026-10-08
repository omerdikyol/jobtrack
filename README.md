# jobtrack

A personal job-search workspace built from your Gmail recruiting mail. Find applications, assessments, interviews, offers, and rejections; follow the email timeline; and keep your next steps beside each opportunity.

The application runs locally with Python, FastAPI, and SQLite. Gmail access is read-only. Classification uses offline rules, optionally assisted by a local or hosted model. **Hosted models receive email content when enabled**; rules-only mode and local models keep that content on your device.

## Get started

Python 3.10 or newer is required.

```bash
uv venv
uv pip install -e ".[dev]"
source .venv/bin/activate

jobtrack auth --credentials ~/Downloads/client_secret.json
jobtrack serve
```

Open [the workspace](http://127.0.0.1:8765). If you have already authenticated, start with `jobtrack serve`; your existing token and database are reused.

The interface is deliberately dense: charcoal surfaces, amber controls, monospace typography, compact top navigation, and packed panels. The header toggle provides a warm light alternative.

The app includes:

- **Overview:** current stages, active opportunities, interview count, response rate, an eight-week activity chart, role categories, follow-ups, and recent mail.
- **Applications:** searchable, sortable list and board views; stage filters; CSV export of the current view; historical snapshots.
- **Application details:** chronological email history, **Open in Gmail** links to the original conversation, classification confidence, notes, follow-up date, and optional status correction.
- **Activity:** recent recruiting messages and persistent sync history.
- **Sync options:** lookback, upper date bound, maximum messages, custom Gmail search, reclassification, and a preview that leaves application history unchanged.
- **Settings:** provider connection cards, masked API keys, live model discovery, a searchable multi-model picker, consensus review, and a sample-email team test.

Use `/` to jump to application search. Dialogs support Escape, keyboard navigation, and native focus containment. The layout adapts to narrow screens without a separate mobile app.

### Preview before importing

Open **Sync options**, set a lookback and a small email limit, then choose **Preview first**. You can inspect up to 100 candidate messages and their classifications. Previews are recorded in sync history, but do not add or remove applications, events, ignored-message cache entries, or the last successful import timestamp.

**Sync Gmail** in the header scans up to 100 messages using your saved lookback. Use the sync dialog to change the limit; `0` means unlimited. Increasing the cap will continue past previously processed mail, which is skipped without downloading its body again.

A limit applies to **search results**, including messages already processed. To reach older mail in a large backfill, increase the limit or use the Before date; repeating the same capped search does not paginate past the cap.

### Corrections and metrics

User notes, follow-up dates, and status corrections are stored on the application. A sync updates its email history without clearing those fields. Select **Automatic · from email** to clear a status correction and return to the derived status.

The response rate counts applications with an assessment, interview, rejection, or offer email, divided by applications with a submission or one of those response events. Multiple emails for the same application do not inflate the rate. Outreach and incomplete applications are excluded from this denominator. The chart counts applications by the week they were first observed, not the number of messages.

**On your radar** includes due follow-ups, interviews and assessments in progress, incomplete applications, and submitted applications with no update for at least 14 days. These are attention cues, not inferred interview dates or deadlines.

Historical snapshots replay **email-derived** status as of the selected UTC date. Present-day notes, follow-up dates, and manual status corrections are omitted from historical views, and editing is available only in the current view. Company and role labels are current identity metadata.

Use the sun/moon button in the header to switch between light and dark mode. Your choice is saved on this browser; before you choose, the workspace follows your system theme.

Employment and freelance work have separate workspace views, metrics, activity, and CSV exports. Messages from known freelance platforms or subjects explicitly mentioning freelance work are categorized automatically. Change **Work category** in the application details to correct a category; sync preserves your correction. Existing timelines are migrated without changing message or application IDs. A contract employment role is not automatically treated as freelance.

**Applications you sent yourself** are tracked when **Track applications you sent** is on, in **Sync options** or **Workspace settings**. A dedicated `from:me` search runs first, because the inbound search returns thousands of ids and would otherwise use up the message limit. Mail from your own address whose subject announces an application — `Application for…`, `CV for…`, `iş başvurusu`, `özgeçmiş` — is recorded as the `applied` submission it is, using **rules only**: no model sees it, so it costs nothing and cannot misattribute it. The employer comes from the subject first, then the recipient's domain, including the employer in an ATS local part (`novastudio@jobs.workablemail.com`) and multi-part suffixes (`kalemci.com.tr`). Recipients on free mail hosts are skipped, since a person is not an employer, as is any domain too short to be a real label. A reply inside an existing thread joins that application. Mail you sent that is not an application stays ignored exactly as before, and enabling the option un-forgets the messages earlier runs cached as ignored.

Normal **Sync Gmail** fetches and analyzes only messages not previously processed, including skipping old ignored mail and saved unresolved reviews after model or parser changes. Gmail search still lists lightweight message IDs to discover new mail; skipped bodies are never downloaded or sent to models. The normal-sync message limit counts new mail, not skipped IDs.

The arrow beside **Sync Gmail** opens **Sync all**, which rechecks all matching recruiting mail across all dates, without a message limit. The menu also opens **Sync options**, where **Sync mode → Full sync · recheck old emails** lets you choose a date range or limit instead. Preview obeys the selected mode but does not mark any mail as synced.

### Model connections and consensus

Put provider keys in the project `.env` (see `.env.example`), or enter them in **Workspace settings → Connections**. NVIDIA NIM uses `NVIDIA_NIM_API_KEY` and its OpenAI-compatible hosted endpoint. Keys are read without exporting them to other processes; changing `.env` takes effect on the next request. Saved keys override process environment keys, which override `.env`. HTTPS uses verified system and certifi trust roots.

Open **Workspace settings → Connections** to save a key and endpoint independently for each provider. **Save & discover models** asks that connection for its current text model IDs. NVIDIA NIM appears first. OpenRouter discovery only includes zero-price text models with explicit `:free` IDs or `openrouter/free`; there is no paid fallback. Other providers’ free tier or trial access depends on your account and quotas, not just a model ID. Discovery is live, so a listed model is not a guarantee of inference access; test the team before syncing. In **Review team**, select models across providers or add an exact custom model ID. Single-model mode uses the first selection; **Make primary** changes it. Consensus mode requires two to five distinct selections.

Consensus starts with independent calls in parallel. If reviewers disagree on relevance, event, employer, or role, each sees the peer verdicts and rechecks the original email. Up to one, two, or three discussion rounds can follow. Each round has a 45-second wait limit; timed-out requests can finish in the background but their late answers are discarded. Provider failures stop discussion rather than being counted as agreement. Only unanimous agreement is accepted, with the lowest reviewer confidence. Agreement is not a guarantee of correctness. A unanimous employer that conflicts with an explicit LinkedIn application header is flagged for manual review and cannot move the message to another company. Same-thread imports respect known employer and role differences; manual company renames retain the previous name for follow-ups in that thread.

Unresolved reviews retain rule results and are visibly flagged. Rechecking an unresolved message preserves its existing event. They are kept in **Recent consensus decisions**, including unrelated mail without an event, and in the application timeline when an event is recorded. Dry runs show opinions but do not persist decisions. Hosted reviewers receive the selected email and a short related-email context; multiple model calls may incur provider charges. Keys remain in `.env` or the private local settings file, never in the browser response or review audit.

LinkedIn submission templates and company recruiting emails are both searched. New imports use Gmail's receipt timestamp (including milliseconds), process fetched messages oldest first, and provide previous related events to model reviewers. Matching uses thread and employer/role identity; close arrival times alone never merge different applications. Each message keeps its own timeline event even when two confirmations describe one application. The timeline displays date, time, seconds, and local time zone. The batch being imported is held in memory for chronological sorting; apply a message limit for very large backfills.

## Architecture

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

No JavaScript build server, database daemon, external asset CDN, queue service, or additional runtime dependency is required.

### Persistence and import invariants

- A Gmail message is recorded at most once, keyed by its message ID.
- Event replacement, derived status refresh, and orphan cleanup occur in one SQLite transaction. A failed replacement rolls back to the original event.
- A Gmail thread identifies an application first. Outside that thread, distinct known roles at a company stay separate. Missing identity is attached only when the match is unambiguous; unknown employers do not share a company-wide bucket.
- SQLite WAL allows reading during an import, with a 30-second busy timeout for competing short writes.
- A database reservation prevents the browser, CLI, and scheduler from importing simultaneously.
- Import history retains start/finish times, progress, summary, and failures. Runs whose local owner process has stopped are marked interrupted; retrying resumes safely through message deduplication.
- Your own mail is dropped unless **Track applications you sent** is on, in which case only an application-shaped subject is recorded, by rules, from the recipient rather than the sender.
- Known event IDs are skipped before body download. Ignored IDs are cached by classifier revision and model configuration; reclassification bypasses that cache. A provider failure is never cached as an irrelevant verdict.
- Reclassification can remove a false positive if the analyzer now decides it is unrelated. An unavailable model falls back to rules.
- Derived status follows the furthest recorded lifecycle stage: offer, rejection, interview, assessment, applied, outreach, incomplete. A manual correction takes precedence in the current view.

Migrations preserve existing application IDs and email history. Before upgrading a personal database, use SQLite's backup API or `.backup` while the app is running, or copy the database **and its WAL** after stopping all processes. Restore the backup together with the corresponding source version to roll back. Do not reset the database to migrate it.

## Command line

```bash
jobtrack sync --since 90 --limit 100 --dry-run --llm-mode off
jobtrack sync --since 90 --limit 100
jobtrack sync --since 2023-01-01 --before 2024-01-01
jobtrack sync --since 0 --limit 500
jobtrack sync --recheck
jobtrack sync --track-sent
jobtrack sync --query 'from:lever.co newer_than:90d'

jobtrack list
jobtrack list --category employment
jobtrack list --category freelance
jobtrack stats --category freelance
jobtrack export --category freelance --format csv -o freelance.csv
jobtrack list --active
jobtrack list --status interview --status offer
jobtrack list --company stripe
jobtrack show 7
jobtrack show northwind
jobtrack stats
jobtrack list --as-of 2025-06-01
jobtrack show 7 --as-of 2025-06-01
jobtrack stats --as-of 2025-06-01

jobtrack export --format csv -o applications.csv
jobtrack export --format json --with-events -o applications.json
jobtrack llm-check
jobtrack serve --port 9000 --no-browser
jobtrack schedule install --at 08:00
jobtrack schedule status
jobtrack schedule uninstall
jobtrack logout
```

`--since` accepts a number of days, `7d`, `3m`, `2y`, or a calendar date. Dates accept `YYYY-MM-DD`, `YYYY/MM/DD`, and `DD.MM.YYYY`. `--before` bounds the other end. `--as-of` replays the stored history; it does not refetch Gmail.

Global `--db PATH` selects another database. Global `--json` renders machine-readable results. `jobtrack reset --yes` removes local tracked history; it is unrelated to migrations and does not clear your saved provider keys.

Scheduled sync uses launchd on macOS and cron on Linux. It scans the last seven days with `--no-auth`, refreshing an existing token but never starting interactive sign-in. Check `jobtrack schedule status` and `sync.log` for unattended failures.

## Gmail authorization

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/) and enable the Gmail API.
2. Configure Google Auth Platform with an **External** audience and add your own address as a test user if the app is in Testing.
3. Declare only `https://www.googleapis.com/auth/gmail.readonly` in Data Access.
4. Create a **Desktop app** OAuth client and download its JSON.
5. Run `jobtrack auth --credentials PATH_TO_JSON` and approve the read-only permission.

Testing-mode OAuth refresh tokens can expire after seven days. For an unattended personal app, check the publishing status in Google Auth Platform and reauthorize after changing it. A revoked or expired token is surfaced by sync and `schedule status`; the server does not open an authorization browser by itself.

The web app binds to `127.0.0.1` by default. Binding a network interface requires `--allow-remote`, because there is no separate web login. API responses are not cached, and cross-origin write requests are rejected. API keys are write-only in the browser: it receives only a masked hint. Settings writes are atomic and mode `600`.

## Model analysis

Choose a provider in Settings. Ollama is the default local option; hosted provider adapters include Groq, Gemini, OpenRouter, Cerebras, Mistral, and OpenAI. `local` supports an OpenAI-compatible local server. Use **Refresh available models** to get current IDs from the selected provider.

| Mode | Behavior |
| --- | --- |
| `off` | Offline rules only |
| `auto` | Check the provider; use it as fallback when available |
| `fallback` | Ask the model when rules are uncertain |
| `always` | Let the model decide each unprocessed message |

When the provider repeatedly fails, analysis degrades to rules for the rest of the run and the result explains why. Changing the mode/model affects future unprocessed messages; choose reclassification to reconsider existing events.

Overview role categories follow the same provider. Each distinct role title is grouped once and cached in `role_categories.json`; with no model configured or reachable — or in rules-only mode — keyword rules group the titles instead.

Configuration precedence is **explicit CLI flag → saved settings → environment → provider default**. Saved API keys are kept per provider. Leaving a key blank on save preserves it; **Clear saved key**, followed by Save, removes it.

For a local model:

```bash
ollama serve
ollama pull qwen2.5:3b
jobtrack llm-check
```

Hosted providers use their provider-specific environment variables, or `JOBTRACK_LLM_API_KEY`. General environment controls include `JOBTRACK_LLM_PROVIDER`, `JOBTRACK_LLM_MODEL`, `JOBTRACK_LLM_BASE_URL`, and `JOBTRACK_LLM_MODE`.

## API

Interactive documentation: [API docs](http://127.0.0.1:8765/api/docs).

| Endpoint | Purpose |
| --- | --- |
| `GET /api/export` | Download a category’s current view as a safe CSV attachment |
| `GET /api/health` | Local database version and whether a Gmail token is saved |
| `GET /api/overview?as_of=DATE` | Workspace summary and recent activity |
| `GET /api/applications` | Applications; `status`, `search`, `active`, `as_of` filters |
| `GET /api/applications/{id}?as_of=DATE` | Application and email timeline, including Gmail URLs |
| `PATCH /api/applications/{id}` | Partial company, role, notes, follow-up date, or status correction |
| `GET /api/activity?limit=100&as_of=DATE` | Recent mail; maximum 200 events |
| `GET /api/stats?as_of=DATE` | Status counts and last successful import |
| `POST /api/sync` | `{since, before, limit, recheck, query, dry_run}` |
| `GET /api/sync` | Current/latest job, progress, result, and in-memory preview candidates |
| `GET /api/sync/history` | Latest 20 durable run records |
| `GET /api/settings` | Effective settings with masked keys |
| `PUT /api/settings` | Partial settings update |
| `GET /api/settings/models?provider=NAME` | Provider's live model list |
| `POST /api/settings/test` | Saved team sample test, with round-by-round consensus opinions |
| `GET /api/reviews?status=unresolved` | Latest 30 consensus decisions, optionally filtered by status |

Dates and invalid statuses are rejected before starting a job. Simultaneous import requests return `409`. A conflicting company/role correction also returns `409`. Preview summaries survive restart; the detailed candidate list lives only for the current server process.

## Data and development

Data defaults to `~/Library/Application Support/jobtrack` on macOS, `~/.local/share/jobtrack` on Linux, and `%APPDATA%/jobtrack` on Windows. Override with `JOBTRACK_HOME`.

| File | Purpose |
| --- | --- |
| `jobtrack.db` | Application history, notes, ignored IDs, sync records, and consensus audits |
| `role_categories.json` | Cached role-title to category labels |
| `settings.json` | Provider preferences and keys, kept separately from the database |
| `token.json` | Cached Gmail authorization, mode `600` |
| `credentials.json` | OAuth client credentials |
| `sync.log` | Scheduled import output |

Secrets and databases are git-ignored. Development checks:

```bash
uv pip install -e ".[dev]"
pytest -q
uvx ruff check --select E4,E7,E9,F,I jobtrack tests
```

Tests cover classification, Gmail decoding, model adapters, CLI and scheduling, migrations, transaction rollback, distinct-role grouping, durable job locking/recovery, preview safety, user corrections, metrics, historical replay, and the HTTP API. The browser requires modern ES modules and native HTML dialogs.

The classifier remains heuristic. Missing titles are shown as **Role not stated**, uncertain employers as **Unknown**, and each timeline entry exposes its source and confidence. Gmail links open conversations in your signed-in Gmail account. Different applications with the same company and normalized role may still be combined; multiple Gmail accounts in one database are not supported.
