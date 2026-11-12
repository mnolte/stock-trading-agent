#!/usr/bin/env python3
"""Print a Markdown coverage summary for CI job summaries (item 088).

Reads a backend ``coverage.xml`` (coverage.py) and/or a frontend
``coverage-summary.json`` (Vitest/istanbul) and prints one table with
line and branch percentages per side, the backend's per-package rows,
and the files with the lowest line coverage. Standard library only.

A side whose report is missing or unreadable prints as ``no report``;
the script never exits non-zero for a missing file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path

MIN_STATEMENTS = 50
LEAST_COVERED = 10

_COND_RE = re.compile(r"\((\d+)/(\d+)\)")


@dataclass
class FileCov:
    path: str
    side: str
    lines_pct: float | None
    lines_total: int
    lines_missed: int
    branches_pct: float | None


@dataclass
class SideCov:
    name: str
    lines_pct: float | None = None
    branches_pct: float | None = None
    files: list[FileCov] = field(default_factory=list)
    # package name -> [lines_valid, lines_covered, branches_valid, branches_covered]
    packages: dict[str, list[int]] = field(default_factory=dict)


def _pct(covered: int, total: int) -> float | None:
    return covered / total * 100 if total else None


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _package_of(filename: str) -> str:
    """Top-level package under ``app/``.

    coverage.py reports filenames relative to the source dir, so
    ``broker/alpaca.py`` means ``app/broker/alpaca.py``; a leading
    ``app/`` is tolerated for reports built another way.
    """
    parts = Path(filename).parts
    if "app" in parts:
        idx = parts.index("app")
        # A file directly under app/ has no package component.
        return parts[idx + 1] if idx + 2 < len(parts) else "(root)"
    return parts[0] if len(parts) > 1 else "(root)"


def parse_backend_xml(path: Path) -> SideCov | None:
    try:
        # coverage.xml is our own CI artifact, not untrusted input.
        root = ElementTree.parse(path).getroot()  # noqa: S314
    except (OSError, ElementTree.ParseError):
        return None
    side = SideCov(name="Backend")
    lines_valid = lines_covered = 0
    branches_valid = branches_covered = 0
    for cls in root.iter("class"):
        filename = cls.get("filename") or ""
        line_elems = cls.findall("./lines/line")
        valid = len(line_elems)
        covered = 0
        file_br_valid = file_br_covered = 0
        for line in line_elems:
            hits = int(line.get("hits") or 0)
            if hits > 0:
                covered += 1
            cond = line.get("condition-coverage")
            if cond:
                match = _COND_RE.search(cond)
                if match:
                    file_br_covered += int(match.group(1))
                    file_br_valid += int(match.group(2))
        lines_valid += valid
        lines_covered += covered
        branches_valid += file_br_valid
        branches_covered += file_br_covered
        side.files.append(
            FileCov(
                path=filename,
                side="backend",
                lines_pct=_pct(covered, valid),
                lines_total=valid,
                lines_missed=valid - covered,
                branches_pct=_pct(file_br_covered, file_br_valid),
            )
        )
        pkg = _package_of(filename)
        bucket = side.packages.setdefault(pkg, [0, 0, 0, 0])
        bucket[0] += valid
        bucket[1] += covered
        bucket[2] += file_br_valid
        bucket[3] += file_br_covered
    side.lines_pct = _pct(lines_covered, lines_valid)
    side.branches_pct = _pct(branches_covered, branches_valid)
    return side


def parse_frontend_summary(path: Path) -> SideCov | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    side = SideCov(name="Frontend")
    total = data.get("total")
    if isinstance(total, dict):
        lines = total.get("lines") or {}
        branches = total.get("branches") or {}
        side.lines_pct = _as_pct(lines.get("pct"))
        side.branches_pct = _as_pct(branches.get("pct"))
    for key, entry in data.items():
        if key == "total" or not isinstance(entry, dict):
            continue
        lines = entry.get("lines") or {}
        branches = entry.get("branches") or {}
        statements = entry.get("statements") or {}
        lines_total = int(lines.get("total") or 0)
        lines_covered = int(lines.get("covered") or 0)
        side.files.append(
            FileCov(
                path=key,
                side="frontend",
                lines_pct=_as_pct(lines.get("pct")),
                lines_total=int(statements.get("total") or lines_total),
                lines_missed=lines_total - lines_covered,
                branches_pct=_as_pct(branches.get("pct")),
            )
        )
    return side


def _as_pct(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def render(backend: SideCov | None, backend_requested: bool,
           frontend: SideCov | None, frontend_requested: bool) -> str:
    out: list[str] = ["## Coverage", ""]
    out.append("| Side | Lines | Branches |")
    out.append("| --- | ---: | ---: |")
    for label, side, requested in (
        ("Backend", backend, backend_requested),
        ("Frontend", frontend, frontend_requested),
    ):
        if not requested:
            continue
        if side is None:
            out.append(f"| {label} | no report | |")
        else:
            out.append(
                f"| {label} | {_fmt_pct(side.lines_pct)} | {_fmt_pct(side.branches_pct)} |"
            )
    out.append("")

    if backend is not None and backend.packages:
        out.append("### Backend coverage by package")
        out.append("")
        out.append("| Package | Lines | Branches |")
        out.append("| --- | ---: | ---: |")
        for pkg in sorted(backend.packages):
            lv, lc, bv, bc = backend.packages[pkg]
            out.append(f"| `{pkg}` | {_fmt_pct(_pct(lc, lv))} | {_fmt_pct(_pct(bc, bv))} |")
        out.append("")

    candidates = [
        f for side in (backend, frontend) if side
        for f in side.files
        if f.lines_total >= MIN_STATEMENTS and f.lines_pct is not None
    ]
    candidates.sort(key=lambda f: (f.lines_pct or 0.0, f.path))
    if candidates:
        out.append(f"### Least covered files (≥ {MIN_STATEMENTS} statements)")
        out.append("")
        out.append("| File | Side | Lines | Missed |")
        out.append("| --- | --- | ---: | ---: |")
        for f in candidates[:LEAST_COVERED]:
            out.append(
                f"| `{f.path}` | {f.side} | {_fmt_pct(f.lines_pct)} | {f.lines_missed} |"
            )
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, help="backend coverage.xml")
    parser.add_argument(
        "--frontend", type=Path, help="frontend coverage-summary.json"
    )
    args = parser.parse_args(argv)

    backend = (
        parse_backend_xml(args.backend) if args.backend is not None else None
    )
    frontend = (
        parse_frontend_summary(args.frontend)
        if args.frontend is not None
        else None
    )
    print(
        render(
            backend,
            backend_requested=args.backend is not None,
            frontend=frontend,
            frontend_requested=args.frontend is not None,
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
