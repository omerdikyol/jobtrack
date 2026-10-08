# Security

## What this app holds

`jobtrack` runs on your own machine and asks for one narrow permission:
`gmail.readonly`. It cannot send, delete, or modify mail.

It stores three things on disk:

| File | Contents | Mode |
| --- | --- | --- |
| `token.json` | Your cached Gmail authorization | `600` |
| `settings.json` | Provider API keys you entered | `600` |
| `credentials.json` | Your OAuth client secret, if you copied it in | as you saved it |

Nothing is uploaded anywhere by the app itself. If you configure a hosted model
provider (Groq, Gemini, OpenRouter, NVIDIA NIM, and so on), the email being
classified **is** sent to that provider for the length of the request. Rules-only
mode (`--llm-mode off`) and local models keep that content on your machine.

The web server binds to `127.0.0.1` and has no login of its own. Anyone who can
reach that port can read your tracked applications and change local settings, so
do not bind it to a network interface unless you understand why you are doing it.

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue.
Use GitHub's **Security → Report a vulnerability** on this repository. Include
the version or commit, what you expected, and what happened instead.

You should get an acknowledgement within a few days. Fixes for credential
handling or token exposure ship as a patch release.

## Hardening your own install

- Keep `jobtrack.db` out of version control. It is git-ignored here, and it
  contains your whole application history.
- Do not commit `.env`, `credentials.json`, or `token.json`. All are ignored.
- If a token is ever exposed, revoke it at
  [myaccount.google.com/permissions](https://myaccount.google.com/permissions),
  then run `jobtrack logout` to clear the local copy.
- Prefer rules-only or a local model if the content of your mail is sensitive.