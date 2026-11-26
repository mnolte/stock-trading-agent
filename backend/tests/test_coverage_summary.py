"""Item 088: scripts/coverage_summary.py prints the CI coverage summary.

Small fixture reports (coverage.xml files and istanbul report dirs) prove
the Unit/E2E/All table, the backend per-package rows on All, the
50-statement cut for the least-covered list, the only-e2e list, and that
a missing report prints ``no report`` without a non-zero exit.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "coverage_summary.py"

_spec = importlib.util.spec_from_file_location("coverage_summary", SCRIPT)
assert _spec is not None and _spec.loader is not None
coverage_summary = importlib.util.module_from_spec(_spec)
# dataclasses resolve cls.__module__ through sys.modules at decoration time.
sys.modules[_spec.name] = coverage_summary
_spec.loader.exec_module(coverage_summary)


def _backend_xml(path: Path, covered: int, total: int) -> None:
    """One file ``engine/big.py`` with ``total`` lines, ``covered`` hit."""
    lines = "".join(
        f'<line number="{n}" hits="{1 if n <= covered else 0}"/>'
        for n in range(1, total + 1)
    )
    path.write_text(
        '<?xml version="1.0" ?>\n'
        '<coverage lines-valid="'
        + str(total)
        + '" lines-covered="'
        + str(covered)
        + '" line-rate="0" branches-covered="0" branches-valid="0" '
        'branch-rate="0">\n'
        "\t<packages>\n\t\t<package name=\"app\">\n\t\t\t<classes>\n"
        '            <class name="app.engine.big" filename="engine/big.py" '
        'line-rate="0" branch-rate="0">\n'
        f"              <lines>\n{lines}\n              </lines>\n"
        "            </class>\n"
        "\t\t\t</classes>\n\t\t</package>\n\t</packages>\n</coverage>\n"
    )


def _frontend_dir(path: Path, pct: float, statements: int) -> None:
    """An istanbul report dir: summary totals + a 60-statement file."""
    path.mkdir(parents=True, exist_ok=True)
    covered = round(statements * pct / 100)
    statement_map = {
        str(i): {"start": {"line": i + 1, "column": 0}, "end": {"line": i + 1, "column": 1}}
        for i in range(statements)
    }
    hits = {str(i): 1 if i < covered else 0 for i in range(statements)}
    (path / "coverage-summary.json").write_text(
        json.dumps(
            {
                "total": {
                    "lines": {"total": statements, "covered": covered, "pct": pct},
                    "branches": {"total": 0, "covered": 0, "pct": 100.0},
                    "statements": {"total": statements, "covered": covered, "pct": pct},
                    "functions": {"total": 0, "covered": 0, "pct": 100.0},
                }
            }
        )
    )
    (path / "coverage-final.json").write_text(
        json.dumps(
            {
                "src/widget.ts": {
                    "path": "src/widget.ts",
                    "statementMap": statement_map,
                    "s": hits,
                    "fnMap": {},
                    "f": {},
                    "branchMap": {},
                    "b": {},
                    "statements": {"total": statements, "covered": covered, "pct": pct},
                    "branches": {"total": 0, "covered": 0, "pct": 100.0},
                }
            }
        )
    )


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    assert coverage_summary.main(list(args)) == 0
    return capsys.readouterr().out


def test_summary_three_columns_and_lists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "report"
    report.mkdir()
    unit_xml = report / "backend-unit.xml"
    e2e_xml = report / "backend-e2e.xml"
    all_xml = report / "backend-all.xml"
    _backend_xml(unit_xml, covered=0, total=60)  # 0% in unit
    _backend_xml(e2e_xml, covered=30, total=60)  # 50% in e2e
    _backend_xml(all_xml, covered=30, total=60)
    fe_unit = tmp_path / "fe-unit"
    fe_e2e = tmp_path / "fe-e2e"
    fe_all = tmp_path / "fe-all"
    _frontend_dir(fe_unit, pct=0.0, statements=60)
    _frontend_dir(fe_e2e, pct=40.0, statements=60)
    _frontend_dir(fe_all, pct=40.0, statements=60)

    out = _run(
        capsys,
        "--backend-unit", str(unit_xml),
        "--backend-e2e", str(e2e_xml),
        "--backend-all", str(all_xml),
        "--frontend-unit", str(fe_unit),
        "--frontend-e2e", str(fe_e2e),
        "--frontend-all", str(fe_all),
    )

    # Totals: backend 0/30/50% lines; frontend 0/40/40%.
    assert "| Backend | 0.0% | — | 50.0% | — | 50.0% | — |" in out
    assert "| Frontend | 0.0% | 100.0% | 40.0% | 100.0% | 40.0% | 100.0% |" in out

    # Package rows on All.
    assert "### Backend coverage by package (All)" in out
    assert "`engine`" in out

    # Least covered on All.
    assert "### Least covered files" in out
    assert "`engine/big.py`" in out
    assert "`src/widget.ts`" in out

    # Only reached by e2e: both fixture files are 0% unit / >0% e2e,
    # sorted by e2e coverage (widget 40% before big.py 50%).
    section = out.split("### Only reached by e2e", 1)[1]
    assert section.index("`src/widget.ts`") < section.index("`engine/big.py`")


def test_missing_column_prints_no_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unit_xml = tmp_path / "unit.xml"
    _backend_xml(unit_xml, covered=30, total=60)
    out = _run(
        capsys,
        "--backend-unit", str(unit_xml),
        "--backend-all", str(tmp_path / "absent.xml"),
    )
    assert "| Backend | 50.0% | — | no report |  | no report |  |" in out
    frontend_row = next(line for line in out.splitlines() if line.startswith("| Frontend |"))
    assert frontend_row.count("no report") == 3
