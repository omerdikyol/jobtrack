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
not the number of messages. It scrolls back through the whole search (up to three years), shades the
applications that got a reply, and opens a week's applications when selected.
Overview tiles and pipeline stages open the applications behind them; on
Insights, every bar, company, and tile does the same.

Overview is about now: what is open and what needs you. Insights is about
patterns across the search, so the two pages don't repeat each other; role
categories live on Insights.

## On your radar

Includes due follow-ups, interviews and assessments in progress, incomplete
applications, and submitted applications with no update for at least 14 days.
These are attention cues, not inferred interview dates or deadlines.

Interview times and assessment deadlines read from the email come first, so
the soonest thing you need to show up for is at the top.

## No response

A submitted application whose latest email is older than **No response after**
(Workspace settings, default 30 days) reads as **No response**. This is derived
when reading and is never stored: a late reply takes over at once, a status you
set yourself always wins, and `0` turns it off. Historical snapshots measure the
age from the selected date.

## Insights

The Insights page groups applications by company, and shows the funnel, where
submitted applications ended up, reply times, reply rate by channel and role
type, rejection reasons, and the last 26 weeks. **Hide company names** replaces
employers with letters for printing or sharing, and **Markdown** downloads the
same retrospective as `jobtrack report`.

- **Reply time** counts from the first email jobtrack saw for the application
  to its first assessment, interview, rejection, or offer. An application known
  only from its reply has no starting point and is left out.
- **Channel** is inferred from the first email: LinkedIn and job boards by
  domain, other hiring platforms as *Applicant tracking system*, your own
  applications as *Sent by you*, and everything else as *Company email*.
- **Rejection reasons** come from the timeline first (after an interview or
  assessment) and then from phrases in the rejection. A rejection within three
  days of applying, without an earlier reply, counts as likely automated.
- **Reapply cooldown**: a rejection under six months old is flagged in the
  application details, in `jobtrack check`, and when adding an application.

## Interview details

When sync records an interview or assessment, the email body is searched for a
date and time, a relative deadline ("within 72 hours"), the first meeting,
scheduling, or test-platform link, and the format. English and Turkish dates are
understood. Times have no time zone in most mail, so they are shown and exported
as written; **Add to calendar** downloads an `.ics` file in local time. Run
`jobtrack details` once to read mail synced before this existed.

## Manual applications, drafts, and notifications

**Add application** records one that sent no email. It gets a single *Added
manually* event, and later mail from that company and role joins it.

**Draft a follow-up** writes text to copy into your mail client; jobtrack still
cannot send mail. With a model configured, only the company, role, status,
contact name, and the dates, kinds, and subjects of the timeline are sent to it,
never email bodies. Without one, or if it fails, a template is used.

`jobtrack sync --notify` shows a desktop notification for new interviews,
assessments, and offers. The scheduled daily sync passes it; reinstall the
schedule with `jobtrack schedule install` to pick it up.

**Goals** on the overview are your own targets: applications, replies,
assessments, interviews, or offers, per week or month, up to six. Each
application counts once per goal, at the moment it first got there; the streak
is how many periods in a row met the target. Select a goal for what counted. Since your last visit, a banner lists
what arrived.

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