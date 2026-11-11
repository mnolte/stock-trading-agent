# CLAUDE.md

Read `AGENTS.md` first: its rules apply to Claude Code in full. This file only adds what is specific to Claude.

## Context

- Stock Trading Agent is a paper-only trading agent for US stocks on Alpaca. Behaviour is in `docs/SPEC.md`, look and wording in `design/`.
- Marc builds with Devin for implementation and Claude for architecture, reviews and spec work. Expect to be asked to review PRs, check code against the spec, and update docs.

## When reviewing a PR

- Check it against the spec sections for that build step (`docs/README.md` has the map) and against `AGENTS.md` hard rules.
- Look specifically for: network calls in tests, `datetime.now()` in engine code, floats for money, hard-coded thresholds, orders without a stop leg, any trace of the live Alpaca URL, and processes killed by name.
- Also check the lessons from earlier reviews: a market sell sent before the stop and target legs are confirmed cancelled; screenshots or other files written into `design/` (they belong in `docs/screenshots/pr-<n>/`); merged migrations or used prompts that were edited; tests that share one database.
- Check that risk math (stop distance, risk, reward to risk) comes from the one shared function.
- Report findings as: blocking, should fix, nice to have. Quote file and line. End with a reply Marc can paste to the author.

## When changing docs

- Every piece of work with a kickoff is a work item `NNN-type-slug` (`docs/work-items.md`): take the next free number and add its row (status `planned`) in the spec PR.
- Issues close themselves (decided 11 Nov): every kickoff for an item with an issue tells Devin to put `Closes #<issue>` in the (last) build PR; the spec PR says `Refs #<issue>`. Issues I open for planned work name their kickoff and say how they close.
- Work on a `spec/NNN-slug` branch from the latest `main` and open a pull request titled `[NNN] spec: Title` with the label `spec change`. Never push to `main` and never merge; Marc does.
- A pull request is done only when it is ready to merge: every CI check has passed (wait for the running ones) and GitHub reports no conflicts with `main`. If a check fails, fix it on the branch; if `main` moved and conflicts, merge the latest `main` into the branch and resolve. Say in the reply that CI passed and the PR merges cleanly (decided 9 Nov).
- Never edit the root `README.md`; Devin owns it.
- Write each new kickoff as `docs/kickoffs/NNN-type-slug.md` with the first line `# NNN · type · Title`, and refer to items as `NNN` or `[NNN]`, GitHub PRs as `#n`. `docs/kickoffs/` holds only work not yet started: a kickoff moves unchanged to `docs/kickoffs/archive/` in the build PR itself (the first one, if the item takes several; for a spec-only item, my spec PR). Kickoffs refer to themselves by name, not by folder, so they read right in either place. Older kickoffs keep their names there (`pr-<n>.md`, `fix-…`); `archive/DEVIN_KICKOFF.md` holds items 001–028.
- Spec changes go to `docs/SPEC.md` only when Marc asks, plus a dated entry in `docs/CHANGES.md`.
- Design changes go to `design/screens/` and `design/screenshots/`, plus an entry in `design/CHANGES.md`.
- Keep the defaults in `backend/config.example.yaml` in step with the spec, but `main` must boot from it at every commit (`docs/README.md` rule 7). A block whose code doesn't exist yet goes in the kickoff, unless the app is known to ignore it.

## Style

- Plain, short sentences. Concrete numbers over adjectives.
- Name assumptions explicitly and list open questions instead of guessing.
