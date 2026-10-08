# Metrics and corrections

How the workspace decides what to show you, and what happens when the mail
disagrees with it.

## Corrections

User notes, follow-up dates, and status corrections are stored on the
application. A sync updates its email history without clearing those fields.
Select **Automatic · from email** to clear a status correction and return to the
derived status.

## Response rate

The response rate counts applications with an assessment, interview, rejection,
or offer email, divided by applications with a submission or one of those
response events. Multiple emails for the same application do not inflate the
rate. Outreach and incomplete applications are excluded from this denominator.

The volume chart counts applications by the week they were **first observed**,
not the number of messages.

## On your radar

Includes due follow-ups, interviews and assessments in progress, incomplete
applications, and submitted applications with no update for at least 14 days.
These are attention cues, not inferred interview dates or deadlines.

## Historical snapshots

Historical snapshots replay **email-derived** status as of the selected UTC
date. Present-day notes, follow-up dates, and manual status corrections are
omitted from historical views, and editing is available only in the current
view. Company and role labels are current identity metadata.

The same replay is available on the command line with `--as-of`:

```bash
jobtrack list --as-of 2025-06-01
jobtrack show 7 --as-of 2025-06-01
jobtrack stats --as-of 2025-06-01
```

## Employment and freelance

Employment and freelance work have separate workspace views, metrics, activity,
and CSV exports. Messages from known freelance platforms or subjects explicitly
mentioning freelance work are categorized automatically. Change **Work category**
in the application details to correct a category; sync preserves your
correction. Existing timelines are migrated without changing message or
application IDs. A contract employment role is not automatically treated as
freelance.

## Syncing

**Sync Gmail** in the header scans up to 100 messages using your saved lookback.
Use the sync dialog to change the limit; `0` means unlimited. Increasing the cap
will continue past previously processed mail, which is skipped without
downloading its body again.

A limit applies to **search results**, including messages already processed. To
reach older mail in a large backfill, increase the limit or use the Before date;
repeating the same capped search does not paginate past the cap. The
normal-sync message limit counts new mail, not skipped IDs.

Normal sync fetches and analyzes only messages not previously processed,
including skipping old ignored mail and saved unresolved reviews after model or
parser changes. Gmail search still lists lightweight message IDs to discover new
mail; skipped bodies are never downloaded or sent to models.

The arrow beside **Sync Gmail** opens **Sync all**, which rechecks all matching
recruiting mail across all dates, without a message limit. The menu also opens
**Sync options**, where **Sync mode → Full sync · recheck old emails** lets you
choose a date range or limit instead. Preview obeys the selected mode but does
not mark any mail as synced.

### Applications you sent

Tracked when **Track applications you sent** is on, in **Sync options** or
**Workspace settings**. A dedicated `from:me` search runs first, because the
inbound search returns thousands of ids and would otherwise use up the message
limit.

Mail from your own address whose subject announces an application —
`Application for…`, `CV for…`, `iş başvurusu`, `özgeçmiş` — is recorded as the
`applied` submission it is, using **rules only**: no model sees it, so it costs
nothing and cannot misattribute it.

The employer comes from the subject first, then the recipient's domain,
including the employer in an ATS local part (`novastudio@jobs.workablemail.com`)
and multi-part suffixes (`kalemci.com.tr`). Recipients on free mail hosts are
skipped, since a person is not an employer, as is any domain too short to be a
real label. A reply inside an existing thread joins that application. Mail you
sent that is not an application stays ignored exactly as before, and enabling
the option un-forgets the messages earlier runs cached as ignored.

## Limits

The classifier remains heuristic. Missing titles are shown as **Role not
stated**, uncertain employers as **Unknown**, and each timeline entry exposes
its source and confidence. Different applications with the same company and
normalized role may still be combined; multiple Gmail accounts in one database
are not supported.