# Privacy

`jobtrack` is self-hosted software. There is no JobTrack server and no project
operator: you run the app on your own machine, and the only account involved is
your own Google account, which you authorize yourself.

## What it accesses

One Google permission, `https://www.googleapis.com/auth/gmail.readonly`. It can
read mail. It cannot send, delete, label, or modify anything.

Sync reads only messages that match the search you configure in the app (a
lookback window and a query). Nothing else is fetched.

## What it stores

Everything is a file on your machine, under the app's data directory:

| File | Contents | Mode |
| --- | --- | --- |
| `jobtrack.db` | Applications parsed from your mail: company, role, dates, status | as you saved it |
| `token.json` | Cached Gmail authorization and refresh token | `600` |
| `settings.json` | Your choices, and provider API keys if you entered any | `600` |
| `credentials.json` | Your OAuth client secret, if you copied it in | as you saved it |

There is no telemetry, no analytics, no crash reporting, and no account with the
project. The web UI binds to `127.0.0.1` and has no login of its own; whoever
can reach that port can read your tracked applications.

## When data leaves your machine

Only if you configure a hosted model provider for classification. In that case
the text of the email being classified is sent to that provider for the length
of the request, under that provider's terms. Rules-only mode and local models
keep everything on your machine. If you never enable a hosted provider, no mail
content leaves your machine at all.

## Google user data

Gmail data is used only to show your own applications back to you. It is not
sold, not used for advertising, not used to train models by the project, and not
transferred to anyone except a hosted provider you configured yourself for
classification. Human beings do not read it. Use of Gmail data adheres to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

## Removing your data

Revoke the app's access at
[myaccount.google.com/permissions](https://myaccount.google.com/permissions),
then delete the app's data directory (`~/Library/Application Support/jobtrack`
on macOS, `~/.local/share/jobtrack` on Linux, or `$JOBTRACK_HOME`). Backups you
made of that directory are yours to remove.

## Contact

Open an issue on this repository, or use **Security → Report a vulnerability**
for anything sensitive. See [SECURITY.md](SECURITY.md).
