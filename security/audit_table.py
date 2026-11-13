#!/usr/bin/env python3
"""Item 084: render the weekly audit table for `run-audits.sh --all`.

Reads the JSON reports the script collected and prints one markdown
table — never exits non-zero on findings (the weekly run is
informational; the per-PR gate is the default mode of run-audits.sh).

Inputs (paths in argv order):
  pip prod.json   pip-audit -f json of the production requirements
  pip dev.json    pip-audit -f json of the dev requirements
  npm prod.json   pnpm audit --prod --json
  npm dev.json    pnpm audit --json (all dependencies)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _load(path: str) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def pip_rows(data: dict | None, scope: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for dep in (data or {}).get("dependencies") or []:
        for vuln in dep.get("vulns") or []:
            rows.append(
                [
                    "python",
                    scope,
                    str(dep.get("name") or "?"),
                    str(vuln.get("id") or "?"),
                    "—",
                ]
            )
    return rows


def npm_rows(data: dict | None, scope: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for adv in ((data or {}).get("advisories") or {}).values():
        rows.append(
            [
                "npm",
                scope,
                str(adv.get("module_name") or "?"),
                str(
                    adv.get("github_advisory_id")
                    or adv.get("id")
                    or adv.get("url")
                    or "?"
                ),
                str(adv.get("severity") or "—"),
            ]
        )
    return rows


def main() -> int:
    pip_prod, pip_dev, npm_prod, npm_dev = sys.argv[1:5]
    rows = (
        pip_rows(_load(pip_prod), "prod")
        + pip_rows(_load(pip_dev), "dev")
        + npm_rows(_load(npm_prod), "prod")
        + npm_rows(_load(npm_dev), "dev")
    )
    print("| Ecosystem | Scope | Package | Advisory | Severity |")
    print("| --- | --- | --- | --- | --- |")
    for row in sorted(rows):
        print("| " + " | ".join(row) + " |")
    if not rows:
        print("| — | — | no findings | — | — |")
    print()
    print(
        f"Audit table: {len(rows)} finding(s) across prod and dev "
        "dependencies (item 084; informational — the PR gate is "
        "`run-audits.sh` without `--all`)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
