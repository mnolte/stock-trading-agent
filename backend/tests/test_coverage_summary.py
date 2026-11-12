"""Item 088: scripts/coverage_summary.py prints the CI coverage summary.

Two small fixture reports (a coverage.xml and a coverage-summary.json)
prove the totals table, the backend per-package rows, the 50-statement
cut for the least-covered list, and that a missing file prints
``no report`` without a non-zero exit.
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


def _backend_xml(path: Path) -> None:
    """66 executable lines: engine/big.py has 60 (10 hit), risk/small.py 4
    (all hit), a root file 2 (all hit); 4 branches, 2 covered."""
    big_lines = "".join(
        f'<line number="{n}" hits="{1 if n <= 10 else 0}"/>' for n in range(1, 61)
    )
    big_lines = big_lines.replace(
        '<line number="2" hits="1"/>',
        '<line number="2" hits="1" branch="true" condition-coverage="50% (1/2)"/>',
    )
    small_lines = "".join(
        f'<line number="{n}" hits="1"/>' for n in range(1, 5)
    ).replace(
        '<line number="2" hits="1"/>',
        '<line number="2" hits="1" branch="true" condition-coverage="50% (1/2)"/>',
    )
    root_lines = '<line number="1" hits="1"/><line number="2" hits="1"/>'
    path.write_text(
        '<?xml version="1.0" ?>\n'
        '<coverage lines-valid="66" lines-covered="16" line-rate="0.24" '
        'branches-covered="2" branches-valid="4" branch-rate="0.5">\n'
        "\t<packages>\n\t\t<package name=\"app\">\n\t\t\t<classes>\n"
        '            <class name="app.engine.big" filename="engine/big.py" '
        'line-rate="0.17" branch-rate="0.5">\n'
        f"              <lines>\n{big_lines}\n              </lines>\n"
        "            </class>\n"
        '            <class name="app.risk.small" filename="risk/small.py" '
        'line-rate="1" branch-rate="1">\n'
        f"              <lines>\n{small_lines}\n              </lines>\n"
        "            </class>\n"
        '            <class name="app.root" filename="root.py" '
        'line-rate="1" branch-rate="1">\n'
        f"              <lines>\n{root_lines}\n              </lines>\n"
        "            </class>\n"
        "\t\t\t</classes>\n\t\t</package>\n\t</packages>\n</coverage>\n"
    )


def _frontend_summary(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "total": {
                    "lines": {"total": 70, "covered": 56, "pct": 80.0},
                    "branches": {"total": 8, "covered": 4, "pct": 50.0},
                    "statements": {"total": 70, "covered": 56, "pct": 80.0},
                    "functions": {"total": 8, "covered": 4, "pct": 50.0},
                },
                "src/a.ts": {
                    "lines": {"total": 60, "covered": 6, "pct": 10.0},
                    "branches": {"total": 4, "covered": 2, "pct": 50.0},
                    "statements": {"total": 60, "covered": 6, "pct": 10.0},
                    "functions": {"total": 4, "covered": 2, "pct": 50.0},
                },
                "src/b.ts": {
                    "lines": {"total": 10, "covered": 10, "pct": 100.0},
                    "branches": {"total": 4, "covered": 2, "pct": 50.0},
                    "statements": {"total": 10, "covered": 10, "pct": 100.0},
                    "functions": {"total": 4, "covered": 2, "pct": 50.0},
                },
            }
        )
    )


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    assert coverage_summary.main(list(args)) == 0
    return capsys.readouterr().out


def test_summary_totals_packages_and_least_covered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    backend_xml = tmp_path / "coverage.xml"
    frontend_json = tmp_path / "coverage-summary.json"
    _backend_xml(backend_xml)
    _frontend_summary(frontend_json)

    out = _run(
        capsys,
        "--backend",
        str(backend_xml),
        "--frontend",
        str(frontend_json),
    )

    # Totals: backend 16/66 lines = 24.2%, 2/4 branches = 50.0%;
    # frontend straight from the summary's pct fields.
    assert "| Backend | 24.2% | 50.0% |" in out
    assert "| Frontend | 80.0% | 50.0% |" in out

    # One row per top-level package under app/, sorted by name.
    assert "### Backend coverage by package" in out
    engine_row = next(line for line in out.splitlines() if "`engine`" in line)
    assert "16.7%" in engine_row  # 10/60
    assert "`(root)`" in out
    # sorted by name: "(root)" < "engine" < "risk"
    assert out.index("`(root)`") < out.index("`engine`") < out.index("`risk`")

    # Least-covered list: src/a.ts (10.0%) before engine/big.py (16.7%);
    # src/b.ts has only 10 statements and must not appear.
    assert "### Least covered files" in out
    assert out.index("`src/a.ts`") < out.index("`engine/big.py`")
    big_row = next(line for line in out.splitlines() if "engine/big.py" in line)
    assert "| 50 |" in big_row  # 60 lines - 10 covered
    assert "`src/b.ts`" not in out


def test_missing_file_prints_no_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _run(capsys, "--backend", str(tmp_path / "absent.xml"))
    assert "no report" in out
    assert "| Frontend |" not in out  # a side not requested is omitted
