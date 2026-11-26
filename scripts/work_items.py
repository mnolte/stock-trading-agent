#!/usr/bin/env python3
"""The work-item register, from one file per item (item 102).

From item 102 on, every work item has its own file
``docs/changes/NNN-type-slug.md``: a header with the register fields, then
the item's change notes. Its design notes, if any, are in
``design/changes/NNN-type-slug.md``. Items up to 101 stay in the table in
``docs/work-items.md``.

Usage:
  scripts/work_items.py            print the whole register as Markdown
  scripts/work_items.py --check    check every item file; exit 1 on a problem

Standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGES = ROOT / "docs" / "changes"
DESIGN_CHANGES = ROOT / "design" / "changes"
REGISTER = ROOT / "docs" / "work-items.md"

FIRST_ITEM = 102
TYPES = ("feat", "fix", "analysis", "ops", "spec")
STATUSES = ("planned", "ready", "building", "merged", "dropped")
FIELDS = ("item", "type", "title", "status", "issue", "kickoff", "spec_prs", "build_prs")
NAME = re.compile(r"^(\d{3})-(feat|fix|analysis|ops|spec)-[a-z0-9]+(?:-[a-z0-9]+){0,5}\.md$")
NONE = ("", "—", "-", "none")


@dataclass
class Item:
    path: Path
    item: int
    type: str
    title: str
    status: str
    issue: int | None
    kickoff: str
    spec_prs: list[int]
    build_prs: list[int]


def _prs(value: str, field: str) -> list[int]:
    value = value.strip()
    if value in NONE or value == "[]":
        return []
    if not (value.startswith("[") and value.endswith("]")):
        raise ValueError(f"{field}: write a list like [303, 305], not {value!r}")
    out = []
    for part in value[1:-1].split(","):
        part = part.strip().lstrip("#")
        if not part.isdigit():
            raise ValueError(f"{field}: {part!r} is not a PR number")
        out.append(int(part))
    return out


def parse(path: Path) -> Item:
    """Read one item file. Raises ValueError with a plain message."""
    m = NAME.match(path.name)
    if not m:
        raise ValueError("file name is not NNN-type-slug.md (type one of " + ", ".join(TYPES) + ")")
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("the file must start with a --- header")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("the header has no closing ---")
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"header line without a colon: {line!r}")
        key = key.strip()
        if key not in FIELDS:
            raise ValueError(f"unknown header field {key!r}")
        if key in fields:
            raise ValueError(f"header field {key!r} twice")
        fields[key] = value.strip()
    missing = [f for f in FIELDS if f not in fields]
    if missing:
        raise ValueError("missing header fields: " + ", ".join(missing))
    if not fields["item"].isdigit():
        raise ValueError("item must be a number")
    item = int(fields["item"])
    if f"{item:03d}" != m.group(1):
        raise ValueError(f"item {item:03d} doesn't match the file name")
    if item < FIRST_ITEM:
        raise ValueError(f"items before {FIRST_ITEM} stay in docs/work-items.md")
    if fields["type"] != m.group(2):
        raise ValueError(f"type {fields['type']!r} doesn't match the file name")
    if fields["status"] not in STATUSES:
        raise ValueError("status must be one of " + ", ".join(STATUSES))
    if not fields["title"]:
        raise ValueError("title is empty")
    issue_raw = fields["issue"].lstrip("#")
    if issue_raw in NONE:
        issue = None
    elif issue_raw.isdigit():
        issue = int(issue_raw)
    else:
        raise ValueError(f"issue must be a number or —, not {fields['issue']!r}")
    kickoff = fields["kickoff"]
    if kickoff not in NONE and kickoff != path.name:
        raise ValueError(f"kickoff must be — or {path.name} (same name as this file)")
    heading = f"# {item:03d} · {fields['type']} · {fields['title']}"
    if heading not in text[end:]:
        raise ValueError(f"the body must have the heading {heading!r}")
    return Item(
        path,
        item,
        fields["type"],
        fields["title"],
        fields["status"],
        issue,
        "" if kickoff in NONE else kickoff,
        _prs(fields["spec_prs"], "spec_prs"),
        _prs(fields["build_prs"], "build_prs"),
    )


def check(folder: Path = CHANGES, design_folder: Path = DESIGN_CHANGES) -> list[str]:
    """Every problem in the item files, as 'file: message' lines."""
    problems: list[str] = []
    seen: dict[int, str] = {}
    names: set[str] = set()
    for path in sorted(folder.glob("*.md")):
        if path.name == "README.md":
            continue
        try:
            it = parse(path)
        except ValueError as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        names.add(path.name)
        if it.item in seen:
            problems.append(f"{path.name}: number {it.item:03d} is also used by {seen[it.item]}")
        seen[it.item] = path.name
    if design_folder.is_dir():
        for path in sorted(design_folder.glob("*.md")):
            if path.name != "README.md" and path.name not in names:
                problems.append(
                    f"design/changes/{path.name}: no docs/changes/{path.name} with the same name"
                )
    return problems


def _cell(prs: list[int]) -> str:
    return ", ".join(f"#{n}" for n in prs) or "—"


def register(folder: Path = CHANGES) -> str:
    """The table of docs/work-items.md, followed by the rows of the item files."""
    text = REGISTER.read_text(encoding="utf-8")
    after = text.split("\n## Register\n", 1)[1].splitlines()
    start = next(i for i, line in enumerate(after) if line.startswith("| "))
    lines = []
    for line in after[start:]:  # the one table under "## Register"
        if not line.startswith("| "):
            break
        lines.append(line)
    rows = []
    for path in sorted(folder.glob("*.md")):
        if path.name == "README.md":
            continue
        it = parse(path)
        title = it.title + (f" (issue #{it.issue})" if it.issue else "")
        kickoff = f"`{it.kickoff}`" if it.kickoff else "—"
        rows.append(
            f"| {it.item:03d} | {it.type} | {title} | {kickoff} | {_cell(it.spec_prs)} | "
            f"{_cell(it.build_prs)} | {it.status} |"
        )
    return "\n".join(lines + rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--check", action="store_true", help="check the item files; exit 1 on a problem"
    )
    args = ap.parse_args(argv)
    if args.check:
        problems = check()
        for p in problems:
            print(p)
        return 1 if problems else 0
    sys.stdout.write(register())
    return 0


if __name__ == "__main__":
    sys.exit(main())
