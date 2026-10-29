# Docs — README rewrite

Kickoff for Devin. Start the session with: "Build docs/kickoffs/docs-readme.md."

```text
Docs PR: rewrite the root README.md. Documentation only: no change to
code behaviour, config defaults, the spec or the design. The current
README is from v0.1 (10 fixed symbols, decision.v1 only, no sign-in) and
is out of date.

Sources of truth, in this order:
- docs/SPEC.md and docs/CHANGES.md for behaviour;
- design/DESIGN.md and design/screens for names and labels;
- AGENTS.md for the rules contributors follow;
- .env.example, backend/config.example.yaml, the Makefile, docs/DEPLOY.md
  and the code for exact names, defaults and commands.

Every statement in the README must be traceable to one of these. Don't
invent features, numbers, commands or claims, and don't add marketing
language. Use the GUI labels exactly as the design writes them
(Settings → API keys, Stop & flatten, Confirm it is you, …). Where the
code in main and the spec disagree, don't pick one: leave the line out
and list it as a question in the PR. Features that are specced but not
yet in main go only in the Roadmap section, worded as in the spec's
build plan.

Structure (in this order):
1. Title "Stock Trading Agent", a one-line description, a short "Paper
   only" notice (from the spec: Alpaca paper account only, no real-money
   orders, a personal experiment and not financial advice), and one hero
   screenshot (the dashboard). Add a CI status badge for the existing
   workflow; no other badges.
2. Table of contents.
3. Description:
   - what it does: the universe and daily liquidity screen, the decision
     cycles (times in New York, with Amsterdam alongside), rules
     pre-filter, the model choosing between code-built options, risk
     gates, bracket orders with broker-side stops, position checks,
     outcome tracking, the SPY benchmark;
   - strategies and shadows, backtests and replays, reviews, insights,
     readiness;
   - a "Safety model" subsection: paper only, every position has a stop
     at Alpaca, the model never overrides code, no shorting and 1×
     margin set at the broker, the kill switch, sign-in when not on
     127.0.0.1;
   - one Mermaid diagram of a single cycle (data → rules → model → risk
     gates → order → stop at Alpaca), using the spec's step names.
4. Screenshots: a gallery (see "Screenshots" below), each with a
   one-line caption saying what the screen is for.
5. Requirements: Python, Node and pnpm versions as pinned in the repo
   (pyproject, .nvmrc/package.json engines, uv); an Alpaca paper account
   with API keys; an OpenAI key; a Finnhub key (free plan); Docker and
   Docker Compose only for the VPS. Name versions exactly as the repo
   pins them.
6. Installation:
   - local: clone, copy .env.example and backend/config.example.yaml,
     make install, make run; the app starts paused; enter the keys in
     Settings → API keys (each is tested before saving); check the
     PAPER badge; reset the paper account to $10,000 in the Alpaca
     dashboard before the first run; press Resume;
   - simulated mode without keys (MODE=simulated);
   - development with make dev;
   - production on a VPS: a short summary and a link to docs/DEPLOY.md
     (don't copy the runbook).
7. Configuration:
   - Environment variables: one table with variable, default, allowed
     values, what it does, and when to change it. Cover every variable
     the app or the deploy files read (.env.example,
     docker-compose*.yml, deploy/*.sh, the settings class in the code).
     Build the list from the code, not from memory; a variable in the
     code but missing from .env.example (or the reverse) is a question
     in the PR.
   - backend/config.yaml: every key in config.example.yaml, grouped by
     top-level block, one collapsible <details> section per block. Each
     row gives the key (full dotted path), default, unit, what it does,
     and whether it can be changed in Settings or only in the file.
     Take the meaning from the spec section that defines it, and link
     that section.
   - Settings and API keys: what is stored where (encrypted keys and
     data/secret.key, settings versions), and that keys never go in
     .env.
8. Usage basics: the daily rhythm in Amsterdam time, explaining the
   weeks when Europe and the US change clocks on different dates; where
   to look (Dashboard, Positions, LLM inspector, Logs, Readiness);
   Pause, Resume and Stop & flatten in one or two sentences each.
9. Commands: the make targets that exist (install, run, dev, test, lint,
   security if present, logs-reindex, create-user, broker-guard,
   replay, …), one line each, generated from the Makefile.
10. Project layout: the top-level folders and what's in each.
11. Contributing:
    - how work is done: spec first (docs/SPEC.md, docs/CHANGES.md), one
      build step per PR, the PR template, screenshots next to the design
      PNGs, design differences listed;
    - a short summary of the hard rules in AGENTS.md, linking it as the
      full list. Include at least: paper only, no network in tests,
      migrations and used prompts immutable, never kill processes by
      name, no restarts during the session, no silent failures, secrets
      never logged;
    - before opening a PR: make lint, make test (and make security if
      present) must pass;
    - reporting a security problem: link docs/SECURITY.md if it exists;
      otherwise say to contact the maintainer privately and not to open
      a public issue.
12. Roadmap and status: merged build-plan steps versus open ones,
    taken from the spec's build plan and git history; no dates or
    promises.
13. License: only if a LICENSE file exists. If not, write no license
    section and ask in the PR.
14. Disclaimer: the paper-only, not-financial-advice text from the
    spec.

Screenshots:
- Capture them from the real app, not from the design files, in
  MODE=simulated with seeded fixture data, so no real account number,
  key, email address, IP address or position appears. Check every image
  for these before committing.
- Add a repeatable script (make readme-screens, Playwright) that starts
  the app in simulated mode and writes the images to
  docs/images/readme/.
- Screens: Dashboard (hero), Decision drawer (decision.v2 options),
  Positions, Performance, Insights, Readiness, LLM inspector, Settings,
  Sign in, and the dashboard at phone width (390 px).
- Desktop at 1440 px wide, cropped to the useful part. Each PNG under
  400 KB (optimise it), with alt text naming the screen.
- Where a screen in main differs visibly from the design, mention it in
  the PR.

Quality bar:
- Plain English, short sentences, consistent terms with the spec.
- Every relative link and image path must resolve: add a test or script
  that checks the links in README.md.
- Add tests/test_readme.py: it fails when a key in
  backend/config.example.yaml or a variable in the settings class is
  missing from README.md, so the README can't fall behind the config.
- Renders cleanly on GitHub (check the Mermaid block and the <details>
  sections in the PR preview).

PR: use the PR template. Put the README preview screenshots and the list
of questions (code vs spec differences, missing variables, license) in
the description. Nothing needs a restart.
```
