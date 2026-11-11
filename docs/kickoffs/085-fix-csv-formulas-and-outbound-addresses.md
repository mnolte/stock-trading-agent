# 085 · fix · CSV formulas and outbound addresses

Kickoff for Devin. Start the session with: "Build docs/kickoffs/085-fix-csv-formulas-and-outbound-addresses.md."

Backend only. Spec: `docs/SPEC.md` → Security hardening → Audit of 11 November 2026 → **CSV cells can't be formulas** and **Outbound addresses are checked when used**. Report: `docs/analysis/080-security-audit.md` (S10, S13). No design change. Issues: #247 (S10), #250 (S13).

```text
[085] CSV formulas and outbound addresses. Goal: no exported CSV cell can
run as a formula; the heartbeat, backup and SMTP addresses are checked
when they are used, not only when saved.
Start from the latest main.

Read first:
1. AGENTS.md (hard rule 20) and docs/README.md.
2. docs/SPEC.md → Security hardening (Stored credentials; Audit of
   11 November 2026); docs/CHANGES.md [080].
3. backend/app/api/inspector.py (export_calls), app/api/backtests.py
   (export_csv), every other csv.writer in app/; app/ops/urls.py,
   app/ops/service.py (_http_ping), app/ops/offsite.py,
   app/notifications/service.py (the mailer and the SMTP test).

Move this kickoff to docs/kickoffs/archive/ with git mv in your PR.
Put "Closes #247, closes #250" in the PR description.

1. One CSV writer, app/csv_safe.py: safe_cell(value) returns numbers
   (int, Decimal, float) unchanged and prefixes "'" to a string that
   starts with =, +, -, @, tab or carriage return; writer(buf) wraps
   csv.writer and applies it to every cell. Use it in every export. A
   test walks app/ for csv.writer outside csv_safe.py and fails.
   Tests: a strategy named "=HYPERLINK(…)" exports as "'=HYPERLINK(…)";
   a negative number written as a number stays "-49.30"; a string
   "-1R" becomes "'-1R".

2. Addresses at use (app/ops/urls.py):
   - resolve_public(host) -> the list of addresses, raising UrlRejected
     when any is not public (the existing rule);
   - heartbeat ping: resolve at each ping, connect to the checked
     address with the original Host and SNI (httpx transport with a
     fixed address), no redirects;
   - offsite backup: resolve the endpoint before each restic run and
     fail the run (OffsiteError, shown in Settings → Server) when it
     isn't public. restic resolves by itself, so here the check and the
     use are two lookups: say so in a comment;
   - SMTP: the host must resolve public at save, at test and at send,
     unless security.allow_private_smtp is true (add the key, default
     false, to config.example.yaml and the README table). A refused host
     answers 422 with "must not point at a private address" at save and
     test; at send it counts as a send failure with category
     "address refused".
   Tests with an injected resolver: a name that is public at save and
   private at use is refused at use for all three; allow_private_smtp
   lets 10.0.0.2 through; loopback is refused even then.

Rules: no network in tests (injected resolvers and fake transports);
hard rule 20 unchanged: a stored password never goes to a new
destination.

Done when: the tests pass; no csv.writer outside the helper; make test
and make security pass.
```

## Why

Audit of 11 November. S10: exports write free text raw; a strategy name starting with `=` runs as a formula when the file is opened in Excel. S13: the heartbeat and backup URLs are checked when saved and resolved again at every use; the SMTP host isn't checked at all, so the test button can probe three ports on internal addresses.

## Assumptions

- Nobody relies on a private SMTP relay today; the switch exists for that case.

## Open questions for Marc

None.
