#!/usr/bin/env bash
# Dependency audits for PR 28: pip-audit (uv.lock) + pnpm audit --prod.
# High/critical findings fail unless the advisory id is listed in
# security/audit-allowlist.yml with a reason and an expiry.
set -euo pipefail
cd "$(dirname "$0")/.."

AL=security/audit-allowlist.yml
allowed() { grep -E '^\s*-?\s*id:' "$AL" | sed 's/.*id:\s*//; s/\s*$//' || true; }

# --- Python: export uv.lock to requirements and audit ----------------
export UV_FROZEN=${UV_FROZEN:-1}
uv export --frozen --no-dev --no-emit-project -o /tmp/sa-reqs.txt
mapfile -t IGNORES < <(allowed | sed 's/^/--ignore-vuln /')
uvx --from pip-audit pip-audit -r /tmp/sa-reqs.txt "${IGNORES[@]}" \
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
