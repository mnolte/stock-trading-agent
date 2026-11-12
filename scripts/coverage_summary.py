#!/usr/bin/env python3
"""Print a Markdown coverage summary for the CI ``coverage`` job (item 088).

Reads backend ``coverage.xml`` reports (coverage.py) and frontend report
directories (Vitest/monocart istanbul output: ``coverage-summary.json`` +
``coverage-final.json``) for the Unit, E2E and All variants of each side
and prints one table with line and branch percentages, the backend's
per-package rows on All, the least-covered files, and the files only the
e2e runs reach. Only dependency is defusedxml (dev extra).

A variant whose report is missing or unreadable prints as ``no report``;
the script never exits non-zero for a missing file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import defusedxml.ElementTree as ElementTree

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
class Variant:
    """One coverage column (unit / e2e / all) for one side."""

    lines_pct: float | None = None
    branches_pct: float | None = None
    files: list[FileCov] = field(default_factory=list)
    # backend only: package name -> [lines_valid, lines_covered,
    #                             branches_valid, branches_covered]
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


def parse_backend_xml(path: Path) -> Variant | None:
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError):
        return None
    variant = Variant()
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
        variant.files.append(
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
        bucket = variant.packages.setdefault(pkg, [0, 0, 0, 0])
        bucket[0] += valid
        bucket[1] += covered
        bucket[2] += file_br_valid
        bucket[3] += file_br_covered
    variant.lines_pct = _pct(lines_covered, lines_valid)
    variant.branches_pct = _pct(branches_covered, branches_valid)
    return variant


def _find_report(dirpath: Path, name: str) -> Path | None:
    "Locate ``name`` in ``dirpath`` or one level below (artifact layouts vary)."
    direct = dirpath / name
    if direct.is_file():
        return direct
    for child in sorted(dirpath.iterdir()) if dirpath.is_dir() else []:
        candidate = child / name
        if candidate.is_file():
            return candidate
    return None


def _frontend_path(key: str) -> str:
    """Normalize a coverage-final.json key to ``src/...``.

    Vitest writes absolute paths; monocart writes ``src/...`` after its
    source filter. The e2e-only comparison needs one form.
    """
    idx = key.find("/src/")
    if key.startswith("src/"):
        return key
    if idx >= 0:
        return key[idx + 1 :]
    return key.lstrip("/")


def _frontend_file_cov(key: str, entry: dict) -> FileCov | None:
    """Per-file line coverage from an istanbul coverage-final.json entry."""
    statement_map = entry.get("statementMap") or {}
    hits = entry.get("s") or {}
    lines: dict[int, bool] = {}
    for stmt_id, span in statement_map.items():
        line = (span.get("start") or {}).get("line")
        if not isinstance(line, int):
            continue
        covered = int(hits.get(stmt_id) or 0) > 0
        # A line counts as covered when any statement on it ran.
        lines[line] = lines.get(line, False) or covered
    total = len(lines)
    covered = sum(1 for v in lines.values() if v)
    branches = entry.get("branches") or {}
    return FileCov(
        path=_frontend_path(key),
        side="frontend",
        lines_pct=_pct(covered, total),
        # coverage-final.json entries are raw istanbul objects without a
        # per-file summary; statementMap is the statement count.
        lines_total=len(statement_map),
        lines_missed=total - covered,
        branches_pct=_as_pct(branches.get("pct")),
    )


def _as_pct(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def parse_frontend_dir(dirpath: Path) -> Variant | None:
    summary_path = _find_report(dirpath, "coverage-summary.json")
    final_path = _find_report(dirpath, "coverage-final.json")
    if summary_path is None and final_path is None:
        return None
    variant = Variant()
    if summary_path is not None:
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        total = data.get("total") if isinstance(data, dict) else None
        if isinstance(total, dict):
            lines = total.get("lines") or {}
            branches = total.get("branches") or {}
            variant.lines_pct = _as_pct(lines.get("pct"))
            variant.branches_pct = _as_pct(branches.get("pct"))
    if final_path is not None:
        try:
            data = json.loads(final_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            for key, entry in data.items():
                if key == "total" or not isinstance(entry, dict):
                    continue
                cov = _frontend_file_cov(key, entry)
                if cov is not None:
                    variant.files.append(cov)
    if not variant.files and variant.lines_pct is None:
        return None
    return variant


def _cells(variant: Variant | None) -> tuple[str, str]:
    if variant is None:
        return "no report", ""
    return _fmt_pct(variant.lines_pct), _fmt_pct(variant.branches_pct)


def render(
    backend: dict[str, Variant | None],
    frontend: dict[str, Variant | None],
) -> str:
    out: list[str] = ["## Coverage", ""]
    out.append(
        "| Side | Unit lines | Unit branches | E2E lines | E2E branches "
        "| All lines | All branches |"
    )
    out.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for label, variants in (("Backend", backend), ("Frontend", frontend)):
        cells: list[str] = []
        for name in ("unit", "e2e", "all"):
            lines_cell, branches_cell = _cells(variants.get(name))
            cells += [lines_cell, branches_cell]
        out.append(f"| {label} | {' | '.join(cells)} |")
    out.append("")

    backend_all = backend.get("all")
    if backend_all is not None and backend_all.packages:
        out.append("### Backend coverage by package (All)")
        out.append("")
        out.append("| Package | Lines | Branches |")
        out.append("| --- | ---: | ---: |")
        for pkg in sorted(backend_all.packages):
            lv, lc, bv, bc = backend_all.packages[pkg]
            out.append(
                f"| `{pkg}` | {_fmt_pct(_pct(lc, lv))} | {_fmt_pct(_pct(bc, bv))} |"
            )
        out.append("")

    # Least covered files on All, both sides.
    seen: set[tuple[str, str]] = set()
    all_files = []
    for variant in (backend.get("all"), frontend.get("all")):
        if variant is None:
            continue
        for f in variant.files:
            if (
                f.lines_total >= MIN_STATEMENTS
                and f.lines_pct is not None
                and (f.side, f.path) not in seen
            ):
                seen.add((f.side, f.path))
                all_files.append(f)
    all_files.sort(key=lambda f: (f.lines_pct or 0.0, f.path))
    if all_files:
        out.append(f"### Least covered files (All, ≥ {MIN_STATEMENTS} statements)")
        out.append("")
        out.append("| File | Side | Lines | Missed |")
        out.append("| --- | --- | ---: | ---: |")
        for f in all_files[:LEAST_COVERED]:
            out.append(
                f"| `{f.path}` | {f.side} | {_fmt_pct(f.lines_pct)} | {f.lines_missed} |"
            )
        out.append("")

    # Files only the e2e runs reach: 0% in Unit, above 0% in E2E.
    e2e_only: list[FileCov] = []
    for unit_v, e2e_v in (
        (backend.get("unit"), backend.get("e2e")),
        (frontend.get("unit"), frontend.get("e2e")),
    ):
        if unit_v is None or e2e_v is None:
            continue
        unit_by_path = {f.path: f for f in unit_v.files}
        for f in e2e_v.files:
            if f.lines_total < MIN_STATEMENTS or not f.lines_pct:
                continue
            unit_cov = unit_by_path.get(f.path)
            unit_pct = unit_cov.lines_pct if unit_cov else 0.0
            if unit_pct == 0.0 and f.lines_pct > 0.0:
                e2e_only.append(f)
    e2e_only.sort(key=lambda f: (f.lines_pct or 0.0, f.path))
    if e2e_only:
        out.append(f"### Only reached by e2e (≥ {MIN_STATEMENTS} statements)")
        out.append("")
        out.append("| File | Side | Unit | E2E |")
        out.append("| --- | --- | ---: | ---: |")
        for f in e2e_only[:LEAST_COVERED]:
            out.append(
                f"| `{f.path}` | {f.side} | {_fmt_pct(0.0)} | "
                f"{_fmt_pct(f.lines_pct)} |"
            )
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for side in ("backend", "frontend"):
        for variant in ("unit", "e2e", "all"):
            help_text = (
                "coverage.xml" if side == "backend" else "report directory"
            )
            parser.add_argument(f"--{side}-{variant}", type=Path, help=help_text)
    args = parser.parse_args(argv)

    backend = {
        v: (
            parse_backend_xml(getattr(args, f"backend_{v}"))
            if getattr(args, f"backend_{v}") is not None
            else None
        )
        for v in ("unit", "e2e", "all")
    }
    frontend = {
        v: (
            parse_frontend_dir(getattr(args, f"frontend_{v}"))
            if getattr(args, f"frontend_{v}") is not None
            else None
        )
        for v in ("unit", "e2e", "all")
    }
    print(render(backend, frontend), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
