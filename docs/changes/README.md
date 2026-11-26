# One file per work item

From item 102 on (decided 15 November), every work item has its own file here, named like its kickoff: `NNN-type-slug.md`. It holds the item's register fields and its change notes. Two pull requests for different items never edit the same file, so they don't conflict over the change log or the register.

`docs/CHANGES.md`, `design/CHANGES.md` and the register table in `docs/work-items.md` keep the history up to item 101 and are not extended.

## The file

```markdown
---
item: 102
type: spec
title: One file per work item
status: merged
issue: 302
kickoff: —
spec_prs: [303]
build_prs: []
---

# 102 · spec · One file per work item

## 2026-11-15 · spec PR

What changed and why, in plain sentences: the spec sections, the rules, the decisions.

## 2026-11-18 · build PR #310

What the build changed in behaviour, config or prompts.
```

- **Header fields**, all required, one per line:
  - `item`: the number, three digits;
  - `type`: `feat`, `fix`, `analysis`, `ops` or `spec`;
  - `title`: the item's title in plain English;
  - `status`: `planned` (spec PR open), `ready` (spec merged, not started), `building`, `merged` or `dropped` (with the reason in the body);
  - `issue`: the GitHub issue number, or `—`;
  - `kickoff`: the kickoff's file name (the same as this file's), or `—`;
  - `spec_prs`, `build_prs`: lists like `[303, 305]`, or `[]`.
- **Body:** the heading `# NNN · type · Title`, then one dated section per PR that changes behaviour, spec, config, prompts or design, newest at the bottom. A section names the spec parts it changed, like the entries in `docs/CHANGES.md` did.

## Design notes

Design changes of the item go in `design/changes/NNN-type-slug.md`, with the same name: the heading `# NNN · type · Title`, then one dated section per PR that names the screen files and screenshots it touched, like the entries in `design/CHANGES.md` did.

## Who writes what

- **The spec PR** (Claude) creates the file with `status: ready` (it is true once the spec PR is merged), the issue and the kickoff, and writes the spec section; for a design change also the design file.
- **The first build PR** (Devin) sets `status: building`, adds itself to `build_prs` and writes its section if it changes behaviour, config or prompts.
- **The PR that completes the item** sets `status: merged`. The file reaches `main` only when that PR is merged, so `main` is right.
- An item with neither a spec PR nor a kickoff (a small fix) still gets its file, in its own PR.

## The register

```bash
python3 scripts/work_items.py            # the whole register: the table up to 101, then these files
python3 scripts/work_items.py --check    # checks every file here; CI runs it as a test
```
