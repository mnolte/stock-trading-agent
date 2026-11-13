#!/usr/bin/env bash
# Dependency audits for PR 28, extended by item 084.
#
#   ./security/run-audits.sh          production sets only: pip-audit on
#                                     uv.lock (no dev) + pnpm audit --prod.
#                                     High/critical findings fail unless
#                                     the advisory id is in
#                                     security/audit-allowlist.yml.
#   ./security/run-audits.sh --all    the dev sets too (uv export
#                                     --extra dev, pnpm audit without
#                                     --prod). Prints a markdown table
#                                     and never fails — the weekly CI
#                                     run posts it to the job summary.
set -euo pipefail
cd "$(dirname "$0")/.."

AL=security/audit-allowlist.yml
MODE="${1:-}"
ALL=0
case "$MODE" in
    "") ;;
    --all) ALL=1 ;;
    *) echo "usage: $0 [--all]" >&2; exit 2 ;;
esac

allowed() { grep -E '^\s*-?\s*id:' "$AL" | sed 's/.*id:\s*//; s/\s*$//' || true; }

# --- Python: export uv.lock to requirements and audit ----------------
export UV_FROZEN=${UV_FROZEN:-1}
uv export --frozen --no-dev --no-emit-project -o /tmp/sa-reqs.txt
if (( ALL )); then
    uv export --frozen --extra dev --no-emit-project -o /tmp/sa-reqs-dev.txt
fi

if (( ALL )); then
    # Informational mode: collect JSON reports, render the table, never
    # fail — findings in dev dependencies must not block the weekly run.
    IGNORES=()
    while IFS= read -r id; do IGNORES+=("--ignore-vuln" "$id"); done \
        < <(allowed)
    uvx --from pip-audit --python 3.12 pip-audit -r /tmp/sa-reqs.txt \
        "${IGNORES[@]}" --skip-editable -f json \
        > /tmp/sa-pip-prod.json || true
    uvx --from pip-audit --python 3.12 pip-audit -r /tmp/sa-reqs-dev.txt \
        "${IGNORES[@]}" --skip-editable -f json \
        > /tmp/sa-pip-dev.json || true
    (cd frontend && pnpm audit --prod --json > /tmp/sa-npm-prod.json) || true
    (cd frontend && pnpm audit --json > /tmp/sa-npm-dev.json) || true
    python3 security/audit_table.py \
        /tmp/sa-pip-prod.json /tmp/sa-pip-dev.json \
        /tmp/sa-npm-prod.json /tmp/sa-npm-dev.json
    exit 0
fi

mapfile -t IGNORES < <(allowed | sed 's/^/--ignore-vuln /')
uvx --from pip-audit --python 3.12 pip-audit -r /tmp/sa-reqs.txt "${IGNORES[@]}" \
    --skip-editable || {
        echo "pip-audit found high/critical advisories (allowlist: $AL)"; exit 1; }

# --- Frontend: pnpm audit --prod, filtered by the allowlist ----------
cd frontend
if ! pnpm audit --prod --json > /tmp/sa-audit.json; then
  if command -v python3 >/dev/null; then
    IDS=$(allowed | tr '\n' '|' | sed 's/|$//')
    python3 - "$IDS" <<'PY'
import json, re, sys
data = json.load(open("/tmp/sa-audit.json"))
allowed = set(filter(None, sys.argv[1].split("|"))) if len(sys.argv) > 1 else set()
bad = [a for a in (data.get("advisories") or {}).values()
       if a.get("severity") in ("high", "critical")
       and str(a.get("github_advisory_id") or a.get("id")) not in allowed]
if bad:
    for a in bad:
        print("BLOCKED:", a.get("module_name"), a.get("severity"), a.get("url"))
    sys.exit(1)
PY
  fi
fi
echo "audits clean (allowlist: $AL)"
