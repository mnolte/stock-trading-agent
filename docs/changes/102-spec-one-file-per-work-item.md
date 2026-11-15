---
item: 102
type: spec
title: One file per work item
status: merged
issue: 302
kickoff: —
spec_prs: [999]
build_prs: []
---

# 102 · spec · One file per work item

## 2026-11-15 · spec PR

Marc, 15 Nov: pull requests kept conflicting when several were open. Each one added lines at the same place in `docs/CHANGES.md`, `design/CHANGES.md`, the register table in `docs/work-items.md` and the build plan in `docs/SPEC.md`. Item numbers were also taken twice (072, 086 and 098), because two people took "the next free number" at the same time.

- **One file per work item** from item 102 on: `docs/changes/NNN-type-slug.md` holds the register fields and the change notes; design notes go in `design/changes/` with the same name. The format and who writes what are in `docs/changes/README.md`.
- **Frozen:** `docs/CHANGES.md`, `design/CHANGES.md` and the register table keep the history up to item 101. Open PRs for items up to 101 may still update them until those items are done. The build plan in `docs/SPEC.md` ends at item 58 (work item 101).
- **The register** is printed by `scripts/work_items.py`; `--check` checks every file's name, header and heading, and that no number is used twice. A test in `backend/tests/ci/` runs it.
- **The label gate** (`.github/scripts/check-spec-label.sh`) accepts a changed file in `docs/changes/` as the spec entry and one in `design/changes/` as the design entry, as well as the old files.
- **Numbers are claimed with an issue:** before anything else, open the issue `[NNN] Title`, with NNN one above the highest `[NNN]` in any issue or PR title. If two items still end up with the same number, the later one takes the next free number.
- `AGENTS.md`, `CLAUDE.md`, `docs/README.md` (rules 6, 8, 10), `docs/work-items.md`, `design/DESIGN.md` and the PR template say so.
- **Still conflicts, on purpose:** two PRs that change the same lines of the spec or the same screen file.
