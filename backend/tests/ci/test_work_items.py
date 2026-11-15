"""Tests for the per-item change files and the register (item 102).

scripts/work_items.py lives outside the app tree, so these tests skip in
the production image (they run in CI's checkout and locally).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "work_items.py"

pytestmark = pytest.mark.skipif(
    not SCRIPT.exists(), reason="scripts/work_items.py not in this environment"
)


@pytest.fixture(scope="module")
def wi():
    spec = importlib.util.spec_from_file_location("work_items", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["work_items"] = mod  # dataclasses look the module up
    spec.loader.exec_module(mod)
    return mod


OK = "123-feat-new-thing.md"
KICK = "kickoff: 123-feat-new-thing.md"
GOOD = """---
item: 123
type: feat
title: New thing
status: planned
issue: 400
kickoff: 123-feat-new-thing.md
spec_prs: [401]
build_prs: []
---

# 123 · feat · New thing

## 2026-11-15 · spec PR

Text.
"""


def write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(text, encoding="utf-8")
    return p


def test_the_repository_files_pass(wi) -> None:
    assert wi.check() == []


def test_good_file_parses(wi, tmp_path: Path) -> None:
    it = wi.parse(write(tmp_path, "123-feat-new-thing.md", GOOD))
    got = (it.item, it.type, it.status, it.issue, it.spec_prs, it.build_prs)
    assert got == (123, "feat", "planned", 400, [401], [])


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        ("123-feature-new-thing.md", GOOD, "file name"),
        ("123-feat-New-thing.md", GOOD, "file name"),
        ("124-feat-new-thing.md", GOOD, "doesn't match the file name"),
        (OK.replace("feat", "fix"), GOOD.replace(KICK, "kickoff: —"), "type"),
        (OK, GOOD.replace("status: planned", "status: done"), "status"),
        (OK, GOOD.replace("issue: 400\n", ""), "missing header fields: issue"),
        (OK, GOOD.replace("spec_prs: [401]", "spec_prs: 401"), "spec_prs"),
        (OK, GOOD.replace(KICK, "kickoff: other.md"), "kickoff"),
        (OK, GOOD.replace("# 123 · feat · New thing", "# New thing"), "heading"),
        (OK, GOOD.replace("---\n", "", 1), "--- header"),
        (OK, GOOD.replace("title: New thing", "title: New thing\nowner: me"), "unknown header"),
    ],
)
def test_bad_files_are_refused(wi, tmp_path: Path, name: str, text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        wi.parse(write(tmp_path, name, text))


def test_items_before_102_are_refused(wi, tmp_path: Path) -> None:
    text = GOOD.replace("123", "101").replace("kickoff: 101-feat-new-thing.md", "kickoff: —")
    with pytest.raises(ValueError, match="stay in docs/work-items.md"):
        wi.parse(write(tmp_path, "101-feat-new-thing.md", text))


def test_a_number_used_twice_is_a_problem(wi, tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    write(docs, "123-feat-new-thing.md", GOOD)
    other = GOOD.replace("New thing", "Other thing").replace("new-thing", "other-thing")
    write(docs, "123-feat-other-thing.md", other)
    problems = wi.check(docs, tmp_path / "design")
    assert any("also used by" in p for p in problems), problems


def test_design_file_needs_its_item_file(wi, tmp_path: Path) -> None:
    docs, design = tmp_path / "docs", tmp_path / "design"
    write(docs, "123-feat-new-thing.md", GOOD)
    write(design, "123-feat-new-thing.md", "# 123 · feat · New thing\n")
    write(design, "124-fix-orphan.md", "# 124 · fix · Orphan\n")
    problems = wi.check(docs, design)
    assert problems == [
        "design/changes/124-fix-orphan.md: no docs/changes/124-fix-orphan.md with the same name"
    ]


def test_readme_is_skipped(wi, tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    write(docs, "README.md", "format\n")
    assert wi.check(docs, tmp_path / "design") == []


def test_register_appends_item_rows_to_the_old_table(wi, tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    write(docs, "123-feat-new-thing.md", GOOD)
    out = wi.register(docs)
    assert "| 001 | feat | Skeleton |" in out
    row = "| 123 | feat | New thing (issue #400) | `123-feat-new-thing.md` | #401 | — | planned |"
    assert out.rstrip().endswith(row)
