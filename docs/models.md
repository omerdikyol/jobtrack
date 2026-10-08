# Model analysis

Classification runs from offline rules. A model is optional, and only ever
decides what the rules were unsure about — unless you ask it to decide
everything.

## Choosing a provider

Choose a provider in Settings. Ollama is the default local option; hosted
provider adapters include Groq, Gemini, OpenRouter, Cerebras, Mistral, and
OpenAI. `local` supports an OpenAI-compatible local server. Use **Refresh
available models** to get current IDs from the selected provider.

| Mode | Behavior |
| --- | --- |
| `off` | Offline rules only |
| `auto` | Check the provider; use it as fallback when available |
| `fallback` | Ask the model when rules are uncertain |
| `always` | Let the model decide each unprocessed message |

When the provider repeatedly fails, analysis degrades to rules for the rest of the
run and the result explains why. Changing the mode/model affects future
unprocessed messages; choose reclassification to reconsider existing events.

Configuration precedence is **explicit CLI flag → saved settings → environment →
provider default**. Saved API keys are kept per provider. Leaving a key blank on
save preserves it; **Clear saved key**, followed by Save, removes it.

> **A hosted provider receives the email being classified**, for the length of
> the request. Rules-only mode and local models keep that content on your
> machine. See [SECURITY.md](../SECURITY.md).

## Connections and discovery

Put provider keys in the project `.env` (see `.env.example`), or enter them in
**Workspace settings → Connections**. NVIDIA NIM uses `NVIDIA_NIM_API_KEY` and
its OpenAI-compatible hosted endpoint. Keys are read without exporting them to
other processes; changing `.env` takes effect on the next request. Saved keys
override process environment keys, which override `.env`. HTTPS uses verified
system and certifi trust roots.

Open **Workspace settings → Connections** to save a key and endpoint
independently for each provider. **Save & discover models** asks that connection
for its current text model IDs. NVIDIA NIM appears first. OpenRouter discovery
only includes zero-price text models with explicit `:free` IDs or
`openrouter/free`; there is no paid fallback. Other providers' free tier or
trial access depends on your account and quotas, not just a model ID. Discovery
is live, so a listed model is not a guarantee of inference access; test the team
before syncing. In **Review team**, select models across providers or add an
exact custom model ID. Single-model mode uses the first selection; **Make
primary** changes it. Consensus mode requires two to five distinct selections.

## Consensus

Consensus starts with independent calls in parallel. If reviewers disagree on
relevance, event, employer, or role, each sees the peer verdicts and rechecks
the original email. Up to one, two, or three discussion rounds can follow. Each
round has a 45-second wait limit; timed-out requests can finish in the
background but their late answers are discarded. Provider failures stop
discussion rather than being counted as agreement. Only unanimous agreement is
accepted, with the lowest reviewer confidence. Agreement is not a guarantee of
correctness. A unanimous employer that conflicts with an explicit LinkedIn
application header is flagged for manual review and cannot move the message to
another company. Same-thread imports respect known employer and role
differences; manual company renames retain the previous name for follow-ups in
that thread.

Unresolved reviews retain rule results and are visibly flagged. Rechecking an
unresolved message preserves its existing event. They are kept in **Recent
consensus decisions**, including unrelated mail without an event, and in the
application timeline when an event is recorded. Dry runs show opinions but do
not persist decisions. Hosted reviewers receive the selected email and a short
related-email context; multiple model calls may incur provider charges. Keys
remain in `.env` or the private local settings file, never in the browser
response or review audit.

## Role categories

Overview role categories follow the same provider. Each distinct role title is
grouped once and cached in `role_categories.json`; with no model configured or
reachable — or in rules-only mode — keyword rules group the titles instead.

## Local models

```bash
ollama serve
ollama pull qwen2.5:3b
jobtrack llm-check
```

Hosted providers use their provider-specific environment variables, or
`JOBTRACK_LLM_API_KEY`. General environment controls include
`JOBTRACK_LLM_PROVIDER`, `JOBTRACK_LLM_MODEL`, `JOBTRACK_LLM_BASE_URL`, and
`JOBTRACK_LLM_MODE`.