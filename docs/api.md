# HTTP API

The workspace is served by a local FastAPI app. Interactive documentation is at
`http://127.0.0.1:8765/api/docs` while the server is running.

There is no login, and no public deployment story: the server binds to
`127.0.0.1` unless you pass `--allow-remote`, because anything that can reach
the port can read your tracked applications.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/export` | Download a category's current view as a safe CSV attachment |
| `GET /api/health` | Local database version and whether a Gmail token is saved |
| `GET /api/overview?as_of=DATE` | Workspace summary and recent activity |
| `GET /api/applications` | Applications; `status`, `search`, `active`, `as_of` filters |
| `GET /api/applications/{id}?as_of=DATE` | Application and email timeline, including Gmail URLs |
| `PATCH /api/applications/{id}` | Partial company, role, notes, follow-up date, or status correction |
| `GET /api/activity?limit=100&as_of=DATE` | Recent mail; maximum 200 events |
| `GET /api/stats?as_of=DATE` | Status counts and last successful import |
| `POST /api/sync` | `{since, before, limit, recheck, query, dry_run, track_sent}` |
| `GET /api/sync` | Current/latest job, progress, result, and in-memory preview candidates |
| `GET /api/sync/history` | Latest 20 durable run records |
| `GET /api/settings` | Effective settings with masked keys |
| `PUT /api/settings` | Partial settings update |
| `GET /api/settings/models?provider=NAME` | Provider's live model list |
| `POST /api/settings/test` | Saved team sample test, with round-by-round consensus opinions |
| `GET /api/reviews?status=unresolved` | Latest 30 consensus decisions, optionally filtered by status |

## Behaviour

Dates and invalid statuses are rejected before starting a job. Simultaneous
import requests return `409`. A conflicting company/role correction also returns
`409`. Preview summaries survive restart; the detailed candidate list lives only
for the current server process.

API responses are not cached, and cross-origin write requests are rejected. API
keys are write-only in the browser: it receives only a masked hint. Settings
writes are atomic and mode `600`.