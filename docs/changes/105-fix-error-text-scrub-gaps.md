---
item: 105
type: fix
title: Stored error text — close the remaining scrub gaps
status: merged
issue: 312
kickoff: —
spec_prs: []
build_prs: [313]
---

# 105 · fix · Stored error text — close the remaining scrub gaps

## 2026-11-18 · build PR #313

Follow-up to item 081. Four gaps could let secret text reach stored rows again; all are closed:

- **Stored-row scrub runs every startup** (`app/stored_scrub.py`). The one-time flag row is gone: each boot rewrites `cycles.data_source_health`, `notifications` (subject, body), `events.details` and `ask_messages` through the shared scrubber. A clean run changes nothing and records no event; a run that changes rows writes the error-level `stored_text_scrub` flag, which re-arms the Needs you item "A key appeared in stored error text. Replace the FRED and Finnhub keys". An info flag every boot would have pushed the latest event forward and hidden an unresolved error flag, so clean runs stay silent.
- **Ask writes are scrubbed** (`app/api/ask.py`, `app/ask/service.py`). Thread context, the auto-title (first 60 chars of the question), user message text and context, assistant answer text and notes pass `scrub_text`/`scrub_value` with the registered secrets before they are stored or sent to the model — a pasted key is stored as `[redacted]`.
- **Second line unified** (`app/secrets_service.py`). `scrub()` delegates to `logging_config.scrub_text`, so key-test details and every other caller get the key patterns, not only exact secret values.
- **AST gate widened** (`tests/security/test_safe_error.py`). Besides `str(exc)`/`{exc}` it now catches `except`-bound aliases, `exc.args`/`__cause__` forms, `.format()`, `%` formatting and traceback renderers; names already inside a scrub or logging call (`{safe_error(exc)}`) count as covered, with a commented allowlist for fixed validation sentences (`exc.msg`, pydantic `exc.errors()`).

Spec: `docs/SPEC.md` → Audit of 11 November, error-text rules — the gate description covers the wider forms, a new "Ask writes" bullet records the write-time scrub, and "Stored rows" now describes the every-startup run and the silent clean pass.
